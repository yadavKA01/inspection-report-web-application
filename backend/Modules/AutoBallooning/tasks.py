

import base64
import io
import math
import os
import shutil
import traceback
import cv2
from pathlib import Path
import gc
import json
import fitz
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from PyPDF2 import PdfReader
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from typing import Dict, List, Optional, Tuple
from ultralytics import YOLO
import mongodb as db
from Dependencies.Inference.llm import LLM
from Dependencies.Logger.logger import AppLogger, LoggingLevel


_Llm_instance = None


def _get_llm():
    """Lazy LLM so importing this module does not initialize boto3/Bedrock (avoids heavy botocore scans at startup)."""
    global _Llm_instance
    if _Llm_instance is None:
        _Llm_instance = LLM()
    return _Llm_instance


def _vision_llm_message(image_bytes: bytes, prompt: str, max_tokens: int = 500, temperature: float = 1.0, top_p: float = 0.999):
    """Drawing vision: Groq (USE_FOR_VISION) or Bedrock via ChatWithImage."""
    llm = _get_llm()
    r = llm.ChatWithImage(
        imageBytes=image_bytes,
        prompt=prompt,
        maxTokens=max_tokens,
        temperature=temperature,
        topP=top_p,
    )
    if isinstance(r, dict) and r.get("status") == "FAILED":
        return f"VISION_LLM_FAILED: {r.get('message', '')}"
    return r.get("message", "")


Logger = AppLogger(name="Auto_Ballooning", module_name="AutoBallooning", level=LoggingLevel.INFO)


def _engine_root() -> Path:
    """`backend/` root (contains Resources/, Modules/)."""
    return Path(__file__).resolve().parents[2]


def resolve_autoballoon_weights_path() -> str:
    """
    Absolute path to YOLO .pt weights. Does not depend on process cwd.

    Override: env AUTOBALLOON_YOLO_WEIGHTS=/full/path/to/model.pt

    Otherwise tries, in order:
      Resources/models/AutoBallooningModel.pt
      Resources/models/model8m.pt
      any other Resources/models/*.pt
    """
    root = _engine_root()
    env = (os.environ.get("AUTOBALLOON_YOLO_WEIGHTS") or "").strip()
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            raise FileNotFoundError(
                f"AUTOBALLOON_YOLO_WEIGHTS is set but not a file: {env}"
            )
        return str(p.resolve())

    models_dir = root / "Resources" / "models"
    for name in ("AutoBallooningModel.pt", "model8m.pt"):
        cand = models_dir / name
        if cand.is_file():
            return str(cand)

    if models_dir.is_dir():
        pts = sorted(models_dir.glob("*.pt"))
        if pts:
            Logger.info("Using YOLO weights %s (first .pt in Resources/models)", pts[0])
            return str(pts[0])

    return str(models_dir / "AutoBallooningModel.pt")


_YOLO_MODEL = None
_YOLO_WEIGHTS_PATH: Optional[str] = None


def get_yolo_model():
    """Lazy-load YOLO once so import order / cwd do not break weight resolution."""
    global _YOLO_MODEL, _YOLO_WEIGHTS_PATH
    if _YOLO_MODEL is None:
        path = resolve_autoballoon_weights_path()
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Auto Ballooning YOLO weights not found at {path}. "
                f"Place a .pt file under {_engine_root() / 'Resources' / 'models'} "
                f"or set AUTOBALLOON_YOLO_WEIGHTS."
            )
        Logger.info("Loading YOLO model from %s", path)
        _YOLO_MODEL = YOLO(path)
        _YOLO_MODEL.to("cpu")
        _YOLO_WEIGHTS_PATH = path
    return _YOLO_MODEL


def get_yolo_weights_path_loaded() -> Optional[str]:
    return _YOLO_WEIGHTS_PATH


def yolo_autoballoon_inference_params(long_side: int, is_pdf_input: bool) -> Tuple[int, float]:
    """
    YOLO imgsz / confidence for engineering drawings.

    Large raster exports (e.g. 10k+ px from PDF) need a much larger imgsz than the
    old default (1024). ProcessMultipleViews used imgsz=1024 only, so detection was
    effectively blind on huge PNGs.
    """
    if is_pdf_input:
        infer_conf = 0.20
        infer_imgsz = 1024
        if long_side > 4000:
            infer_imgsz = min(2560, max(1280, long_side // 6))
        return infer_imgsz, infer_conf

    infer_conf = 0.08
    infer_imgsz = 1024
    if long_side > 8000:
        infer_imgsz = min(3200, max(2560, long_side // 4))
    elif long_side > 5000:
        infer_imgsz = min(3200, max(1920, long_side // 4))
    elif long_side > 4000:
        infer_imgsz = min(2880, max(1600, long_side // 5))
    elif long_side > 2500:
        infer_imgsz = min(2560, max(1280, long_side // 5))
    return infer_imgsz, infer_conf


YOLO_TARGET_CLASSES_FOR_CROPS = (
    "Dimensions",
    "GDnT",
    "Notes",
    "Surface_Finish_Symbols",
    "Special_Characteristics",
)


DIMENSION_EXTRACTION_PROMPT = """
# Technical Drawing Dimension & Notes Extraction

You are an expert at extracting dimensional info, GD&T symbols, and notes from engineering drawings and structuring them in a detailed format.

## Rules:
1. IGNORE red balloon numbers – they are only pointers.
2. Extract ONLY black text (dimensions/tolerances/notes) that balloons point to.
3. Extract GD&T callouts / Feature Control Frames (FCFs) in structured format with symbols and datums.
4. Only ONE entry per base balloon number (dimension, GD&T, or note).
5. Extract notes once from the "NOTES" section, no duplicates.
6. If a balloon number points to a NOTE, include it in the "Dimensions" list with `annotationName: "Note"`.
7. For any any encountered surface finish symbol also write the text like Ra, Rz, Rt, .. and then the value in notes section.
8. PLCS or PLC is same as NoOfPlaces. Do not extract it as notes add it to the previous dimension only with intelligence.
### 9. For some drawings instead of decimal points comma is used. So while extracting the dimensions be aware of that and extract accordingly.
### 10. If for any dimension no tolerance is mentioned and it is not enclosed within a box then associate the general tolerance mentioned in the title block for that particular dimension and mention toleranceType as general tolerance.
11. There are some dimensions with keyword STOCK written in paranthesis. In such cases extract the dimension value only and ignore the keyword STOCK, and these dimensions are reference dimensions so mention toleranceType as Reference.

## CRITICAL: Material Condition Symbol Extraction Rules
Material condition symbols ONLY appear in GD&T Feature Control Frames. You MUST:

1. **Extract EXACTLY what you see** - Do NOT add material conditions if they are not present
2. **Distinguish carefully between these symbols:**
   - "Ⓜ" or "M" in a circle = MMC (Maximum Material Condition)
   - "Ⓛ" or "L" in a circle = LMC (Least Material Condition)
   - "Ⓡ" or "R" in a circle = RFS (Regardless of Feature Size)
   - "S" in a circle = RMS (if present)

3. **Where material conditions appear:**
   - After the tolerance value: "0.2 Ⓜ" means tolerance at MMC
   - After datum references: "|A Ⓜ|B|" means datum A at MMC
   - NEVER assume a material condition if the symbol is not explicitly present

4. **Examples of CORRECT extraction:**
   - "⌖0.2|A|B|" → Data: [["⌖"], ["0.2"], ["A"], ["B"]] (NO material condition)
   - "⌖0.2 Ⓜ|A|B|" → Data: [["⌖"], ["0.2 Ⓜ"], ["A"], ["B"]]
   - "⌖0.2|A Ⓜ|B Ⓛ|" → Data: [["⌖"], ["0.2"], ["A Ⓜ"], ["B Ⓛ"]]
   - "⌖0.2 Ⓛ|A Ⓜ|B|" → Data: [["⌖"], ["0.2 Ⓛ"], ["A Ⓜ"], ["B"]]

5. **Common mistakes to AVOID:**
   - Adding "Ⓜ" when no symbol is present
   - Confusing "Ⓛ" (LMC) with "Ⓜ" (MMC)
   - Adding material conditions to regular dimensions (only in GD&T frames)

## Output Format:
Return ONLY valid JSON with this structure:

{{
  "Dimensions": [
    {{
      "balloon_number": "1",
      "data": {{
        "AnnotationType": "GDT",
        "symbol": "⌖",
        "annotationName": "Position",
        "toleranceType": "Tolerance",
        "keyCharacteristic": "SC",
        "Data": [
          [
            ["⌖"],
            ["0.2"],
            ["A"],
            ["B"],
            ["C"]
          ]
        ]
      }}
    }},
    {{
      "balloon_number": "2", 
      "data": {{
        "AnnotationType": "DIM",
        "Unit": "mm",
        "annotationName": "Linear",
        "representation": "185",
        "upperTol": "",
        "lowerTol": "",
        "drawingZone": "",
        "NoOfPlaces": "",
        "designator": "",
        "toleranceType": "Basic",
        "keyCharacteristic": "SC"
      }}
    }},
    {{
      "balloon_number": "10",
      "data": {{
        "AnnotationType": "NOTE",
        "annotationName": "Note",
        "content": "ALL DIMENSIONS ARE IN mm",
        "keyCharacteristic": "SC"
      }}
    }}
  ]
}}

## Classification Rules:
For GD&T Features (use first structure):

- symbol: Original GD&T symbol (⌖, ⊥, —, ↗↗, ▱, etc.)
- annotationName: Full name (Position, Perpendicularity, Straightness, Total Runout, Flatness, Profile of line, etc.)
- toleranceType: Always "Tolerance"
- Data: Nested array with symbol, tolerance value, and datums in separate sub-arrays
- Material conditions: Extract ONLY if explicitly present - "Ⓜ" for MMC, "Ⓛ" for LMC, "Ⓡ" for RFS

For Dimensions (use second structure):

- Unit: "mm" (default)
- annotationName: Type (Linear, Angular, Radial, Diameter, etc.)
- representation: The actual dimension value (without symbols like ⌀, R, °)
- upperTol: Upper tolerance if present (e.g., "+0.05")
- lowerTol: Lower tolerance if present (e.g., "-0.02")
- drawingZone: Leave empty ""
- NoOfPlaces: Leave empty if not mentioned ""
- designator: Leave empty ""
- toleranceType:  "Symmetric" for ±, "Bilateral" if positive and negetive tolerance is not same,
    "Unilateral" if either positive or negative tolerance is 0,
    "Basic" If the dimension is written in a box Note: No tolerance should be extracted for such cases,  
    "Reference" If the dimension is written in parentheses `( )` Note: No tolerance should be extracted for such cases,
    "Limit" If the range of the value is mentioned.

- keyCharacteristic: Some dimensions are more important that other os eithher SC, or CC or some other symbol is present next to that dimension or note. If not present leave that empty.

For Notes (new rule):

- annotationName: "Note"
- content: Full note text
- No other fields required

## GD&T Symbol Mapping:
⌖ → "Position"
⊥ → "Perpendicularity"
— → "Straightness"
↗↗ → "Total Runout"
↗ → "Circular Runout"
▱ → "Flatness"
⌒ → "Profile of line"
⌓  → "Profile of surface"
○ → "Circularity"
⌭ → "Cylindricity"(a circle between 2 parallel lines)
∠ → "Angularity"
◎  → "Concentricity"
⟳  → "Circular Runout"
Ξ →"Symmetry"

## Annotation Name Classification:
Values with ⌀ → "Diameter"
Values with R → "Radial"
Values with ° → "Angular"
Plain numbers → "Linear"
Text in parentheses like (2X) → extract as "Linear" with the base dimension

## Tolerance Extraction:
±0.05 → upperTol: "+0.05", lowerTol: "-0.05", toleranceType: "Symmetric"
+0.1/-0.05 → upperTol: "+0.1", lowerTol: "-0.05", toleranceType: "Bilateral"
+0.05/0 → upperTol: "+0.05", lowerTol: "0", toleranceType: "Unilateral"

## If no tolerance is mentioned for a particular dimension associate the provided general tolerance to it.
Here is the data from the title block for the general tolerances.

{titleBlockData}

## Examples with Material Conditions:

Input: Balloon 3 points to "⌖0.2 Ⓛ|A|B|C"
Output:
{{
  "balloon_number": "3",
  "data": {{
    "AnnotationType": "GDT",
    "symbol": "⌖",
    "annotationName": "Position", 
    "toleranceType": "Tolerance",
    "keyCharacteristic": "",
    "Data": [
      [
        ["⌖"],
        ["0.2 Ⓛ"],
        ["A"],
        ["B"], 
        ["C"]
      ]
    ]
  }}
}}

Input: Balloon 4 points to "⌖0.2|A|B|C" (NO material condition symbol)
Output:
{{
  "balloon_number": "4",
  "data": {{
    "AnnotationType": "GDT",
    "symbol": "⌖",
    "annotationName": "Position", 
    "toleranceType": "Tolerance",
    "keyCharacteristic": "",
    "Data": [
      [
        ["⌖"],
        ["0.2"],
        ["A"],
        ["B"], 
        ["C"]
      ]
    ]
  }}
}}

Input: Balloon 5 points to "⌖0.2 Ⓜ|A Ⓜ|B Ⓛ|C"
Output:
{{
  "balloon_number": "5",
  "data": {{
    "AnnotationType": "GDT",
    "symbol": "⌖",
    "annotationName": "Position", 
    "toleranceType": "Tolerance",
    "keyCharacteristic": "",
    "Data": [
      [
        ["⌖"],
        ["0.2 Ⓜ"],
        ["A Ⓜ"],
        ["B Ⓛ"], 
        ["C"]
      ]
    ]
  }}
}}

Input: Balloon 6 points to "⊥0.05|A" (NO material condition)
Output:
{{
  "balloon_number": "6",
  "data": {{
    "AnnotationType": "GDT",
    "symbol": "⊥",
    "annotationName": "Perpendicularity", 
    "toleranceType": "Tolerance",
    "keyCharacteristic": "",
    "Data": [
      [
        ["⊥"],
        ["0.05"],
        ["A"]
      ]
    ]
  }}
}}

Input: If the symbol mentioned is a surface finish symbol ⌵3.2
Output:
{{
  "balloon_number": "12",
  "data": {{
    "AnnotationType": "Surface Finish",
    "Unit": "µm",
    "annotationName": "<type of symbol (machining required, or material removal or etc.)>",
    "representation": "Ra 3.2",
    "upperTol": "",
    "lowerTol": "",
    "drawingZone": "",
    "NoOfPlaces": "",
    "designator": "",
    "toleranceType": "",
    "keyCharacteristic": ""
  }}
}}

Input: Balloon 8 points to "30-0.1-0.20"
Output:
{{
  "balloon_number": "8",
  "data": {{
    "AnnotationType": "DIM",
    "Unit": "mm",
    "annotationName": "Linear",
    "representation": "29.85",
    "upperTol": "+0.05",
    "lowerTol": "-0.05", 
    "drawingZone": "",
    "NoOfPlaces": "",
    "designator": "",
    "toleranceType": "Limit",
    "keyCharacteristic": ""
  }}
}}

nput: Balloon 7 points to "12,5±0,5"
Output:
{{
  "balloon_number": "7",
  "data": {{
    "AnnotationType": "DIM",
    "Unit": "mm",
    "annotationName": "Linear",
    "representation": "12.5",
    "upperTol": "+0.5",
    "lowerTol": "-0.5", 
    "drawingZone": "",
    "NoOfPlaces": "",
    "designator": "",
    "toleranceType": "Symmetric",
    "keyCharacteristic": "SC"
  }}
}}


Input: Balloon 7 points to "185±0.1"
Output:
{{
  "balloon_number": "7",
  "data": {{
    "AnnotationType": "DIM",
    "Unit": "mm",
    "annotationName": "Linear",
    "representation": "185",
    "upperTol": "+0.1",
    "lowerTol": "-0.1", 
    "drawingZone": "",
    "NoOfPlaces": "",
    "designator": "",
    "toleranceType": "Symmetric",
    "keyCharacteristic": "SC"
  }}
}}

Input: Balloon 2 points to "8x45°±0.1"
Output:
{{
  "balloon_number": "2",
  "data": {{
    "AnnotationType": "DIM",
    "Unit": "mm",
    "annotationName": "Angular",
    "representation": "45",
    "upperTol": "+0.1",
    "lowerTol": "-0.1", 
    "drawingZone": "",
    "NoOfPlaces": "8",
    "designator": "",
    "toleranceType": "Symmetric",
    "keyCharacteristic": ""
  }}
}}

Input: Balloon 10 points to "ALL DIMENSIONS ARE IN mm"
Output:
{{
  "balloon_number": "10",
  "data": {{
    "AnnotationType": "NOTE",
    "annotationName": "Note",
    "content": "ALL DIMENSIONS ARE IN mm",
    "keyCharacteristic": ""
  }}
}}

REMEMBER: 
- Do NOT add material condition symbols if they are not visible in the drawing
- "Ⓜ" (MMC) and "Ⓛ" (LMC) are DIFFERENT - verify carefully which one is present
- Material conditions ONLY apply to GD&T callouts, never to regular dimensions

ONLY output valid JSON. No extra text.
"""


TITLE_BLOCK_EXTRACTION = """
You are an extraction expert. You are provided with the image of a title block from an engineering drawing.
Your task is to return the extracted text in a meaningful and structured manner.
Your task is to return the data present in title block in a structured json format including general tolerances if present.
Extract general tolerance for each range and all precisions.

Strictly follow this output format. No extra text is needed:
{{
 }},
 "general_tolerances": {{
   "linear_dimensions"(for range 1): {{
     "precision_1": "tolerance_value",
     "precision_2": "tolerance_value",
     "precision_n": "tolerance_value"
   }},
   "linear_dimensions"(for range 2): {{
     "precision_1": "tolerance_value",
     "precision_2": "tolerance_value",
     "precision_n": "tolerance_value"
   }},
   "angular_dimensions": {{
     "precision_1": "tolerance_value",
     "precision_2": "tolerance_value", 
     "precision_n": "tolerance_value"
   }},
   "surface_finish": "value_if_present",
   "standard": "tolerance_standard_if_mentioned"
 }}
}}
Return only json data no extra text needed.
"""

DIMENSION_ASSOCIATION_PROMPT = """
You are provide with the ballooned image of an engineering drawing.

And here are some of the Feature control frame ballloon numbers which have material condition and need to be associateed to a dimension in the drawing.
{BalloonNumbers}

Your task is to find the dimension which is to be associated for each of these balloon numbers to calculate the bonus tolerancing.

The associated dimension is usually a dimension related to that particular feature control frame are mostly the hole diamete or the surface thickness.

for each balloon nume return the associate dimensions balloon number.

## Output Format
{{
"balloon_number":associated-dimension<int>,
"balloon_number":associated-dimension<int>,
"balloon_number":associated-dimension<int>,
}}

"""



GRID_SIZE = 500  

def AssociateDimensions(imagePath, associationPrompt):
  with open(imagePath, 'rb') as f:
          image_bytes = f.read()
  dimensions = _vision_llm_message(image_bytes, associationPrompt)
  return dimensions

# def ExtractDimensions(imagePath, dimensionPrompt):
#   with open(imagePath, 'rb') as f:
#           image_bytes = f.read()
#   #print("calling chat with gemini")
#   dimensions = _get_llm().ChatWithGemini(imageBytes=image_bytes , prompt=dimensionPrompt)['message']
#   #print(dimensions)
#   return dimensions


def ExtractDimensions(imagePath, dimensionPrompt):
  #print("calling vertex AI function")
  with open(imagePath, 'rb') as f:
          image_bytes = f.read()
  dimensions = _vision_llm_message(image_bytes, dimensionPrompt)
  #print(dimensions)
  return dimensions


def ExtractTitleBlockData(imagePath):
    #print("Entered title block extraction")
    with open(imagePath, 'rb') as f:
        image_bytes = f.read()
    dimensions = _vision_llm_message(
        image_bytes,
        TITLE_BLOCK_EXTRACTION,
        max_tokens=4000,
        temperature=0.2,
    )
    #print(dimensions)
    return dimensions


def ConvertToJson(input_string):
    try:
        # Clean the string first
        cleaned_string = input_string.strip()
        # Remove markdown code block syntax
        if cleaned_string.startswith('```json'):
            cleaned_string = cleaned_string[7:]
        elif cleaned_string.startswith('```'):
            cleaned_string = cleaned_string[3:]
            
        if cleaned_string.endswith('```'):
            cleaned_string = cleaned_string[:-3]
            
        cleaned_string = cleaned_string.strip()
        
        return json.loads(cleaned_string)
    except json.JSONDecodeError as e:
        Logger.error(f"Invalid JSON format: {e}")
        Logger.error(f"Cleaned string was: {cleaned_string[:100]}...")  # Show first 100 chars for debugging
        return None


def CalculateIntersectionArea(box1, box2):
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])    
    if x2 <= x1 or y2 <= y1:
        return 0    
    return (x2 - x1) * (y2 - y1)

def CalculateBoxArea(box):
    return (box[2] - box[0]) * (box[3] - box[1])

def MergeBoxes(box1, box2, conf1, conf2):   
    w1, w2 = conf1, conf2
    x1 = int((box1[0]*w1 + box2[0]*w2) / (w1 + w2))
    y1 = int((box1[1]*w1 + box2[1]*w2) / (w1 + w2))
    x2 = int((box1[2]*w1 + box2[2]*w2) / (w1 + w2))
    y2 = int((box1[3]*w1 + box2[3]*w2) / (w1 + w2))
    return [x1, y1, x2, y2]
    

def CustomNms(detections, intersection_threshold=0.6, containment_threshold=0.6):
    if len(detections) <= 1:
        return detections

    detections.sort(key=lambda x: x['conf'], reverse=True)
    filtered_detections = []

    for det in detections:
        merged = False
        

        for kept_det in filtered_detections:
            intersection = CalculateIntersectionArea(det['bbox'], kept_det['bbox'])
            det_area = CalculateBoxArea(det['bbox'])
            kept_area = CalculateBoxArea(kept_det['bbox'])
            union_area = det_area + kept_area - intersection

            iou = intersection / union_area if union_area > 0 else 0
            overlap_smaller = intersection / min(det_area, kept_area) if min(det_area, kept_area) > 0 else 0

            if iou > intersection_threshold or overlap_smaller >= containment_threshold:
                kept_det['bbox'] = MergeBoxes(det['bbox'], kept_det['bbox'], det['conf'], kept_det['conf'])
                kept_det['conf'] = max(det['conf'], kept_det['conf'])  # keep stronger confidence
                merged = True
                break

        if not merged:
            filtered_detections.append(det)

    return filtered_detections

def extractBetweenContours(image_path: str, output_dir: str):
    """
    Processes an engineering drawing, finds large parent contours with inner child contours,
    and whites out the region between parent and children.

    Args:
        image_path (str): Path to input image
        output_dir (str): Directory to save output image

    Returns:
        str: Path to saved output image
    """

    # Read the image
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    # Resize for faster processing
    scale = 0.5
    small_img = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(small_img, cv2.COLOR_BGR2GRAY)
    inverted = cv2.bitwise_not(gray)

    # Density check
    _, binary_test = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    content_density = cv2.countNonZero(binary_test) / (image.shape[0] * image.shape[1])

    # Adapt kernel and iterations based on density
    if content_density > 0.15:  # Dense drawing
        kernel = np.ones((3, 20), np.uint8)
        iterations = 1
    elif content_density > 0.08:  # Medium density
        kernel = np.ones((4, 35), np.uint8)
        iterations = 2
    else:  # Sparse drawing
        kernel = np.ones((40, 50), np.uint8)
        iterations = 3
    # Optimized dilation
    dilated = cv2.dilate(inverted, kernel, iterations)
    thickened = cv2.bitwise_not(dilated)

    # Threshold
    _, binary = cv2.threshold(thickened, 200, 255, cv2.THRESH_BINARY_INV)

    # Find contours
    contours, hierarchy = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    image_area = small_img.shape[0] * small_img.shape[1]
    width_threshold = 0.85 * small_img.shape[1]
    height_threshold = 0.85 * small_img.shape[0]

    # Precompute bounding boxes + areas
    contour_data = []
    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, w, h = cv2.boundingRect(contour)
        contour_data.append((area, x, y, w, h))

    between_mask = small_img.copy()

    # Process parent and children contours
    if hierarchy is not None:
        for i, contour in enumerate(contours):
            if hierarchy[0][i][3] == -1:  # Parent contour
                area, x, y, w, h = contour_data[i]

                meets_criteria = (w >= width_threshold and h >= height_threshold)

                first_child_idx = hierarchy[0][i][2]

                if first_child_idx != -1 and meets_criteria:
                    # Parent mask
                    parent_mask = np.zeros(small_img.shape[:2], dtype=np.uint8)
                    cv2.drawContours(parent_mask, [contour], -1, 255, -1)

                    # Combine all child masks
                    child_masks = []
                    child_idx = first_child_idx
                    while child_idx != -1:
                        child_contour = contours[child_idx]
                        child_mask = np.zeros(small_img.shape[:2], dtype=np.uint8)
                        cv2.drawContours(child_mask, [child_contour], -1, 255, -1)
                        child_masks.append(child_mask)
                        child_idx = hierarchy[0][child_idx][0]

                    if child_masks:
                        combined_children = np.maximum.reduce(child_masks)
                        parent_mask = cv2.subtract(parent_mask, combined_children)

                    # White out region between parent & children
                    between_mask[parent_mask == 255] = 255

    # Resize back to original
    final_output = cv2.resize(between_mask, (image.shape[1], image.shape[0]),
                              interpolation=cv2.INTER_LINEAR)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Generate output filename
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    out_path = os.path.join(output_dir, f"{base_name}_between_white.jpg")

    # Save the output
    cv2.imwrite(out_path, final_output)

    return out_path

def mergeNotes(bbox_list, horizontal_gap_threshold=800):
        
    def get_bbox_center_y(bbox):
        return (bbox[1] + bbox[3]) / 2
    
    def get_horizontal_gap(bbox1, bbox2):
        left1, right1 = bbox1[0], bbox1[2]
        left2, right2 = bbox2[0], bbox2[2]
        
        if right1 <= left2:
            # bbox1 is to the left of bbox2
            return left2 - right1
        elif right2 <= left1:
            # bbox2 is to the left of bbox1
            return left1 - right2
        else:
            # Boxes overlap horizontally
            return -min(right1 - left2, right2 - left1)  # Return negative overlap amount
    
    def are_vertically_aligned(bbox1, bbox2):

        threshold = (abs(bbox1[1] - bbox1[3]) + abs(bbox2[1] - bbox2[3]))//4
        center1_y = get_bbox_center_y(bbox1)
        center2_y = get_bbox_center_y(bbox2)
        
        return abs(center1_y - center2_y) <= threshold
    
    def merge_bboxes(bbox1, bbox2):
        min_x = min(bbox1[0], bbox2[0])
        min_y = min(bbox1[1], bbox2[1])
        max_x = max(bbox1[2], bbox2[2])
        max_y = max(bbox1[3], bbox2[3])
        return [min_x, min_y, max_x, max_y]
    
    # Create a copy of the list to avoid modifying the original
    result_list = [entry.copy() for entry in bbox_list]
    for entry in result_list:
        entry['bbox'] = entry['bbox'].copy()
    
    # Track indices to remove
    indices_to_remove = set()
    
    # Find all Notes entries (class 2)
    notes_indices = [i for i, entry in enumerate(result_list) if entry['class'] == 2]
    
    target_classes = [0, 1, 5, 6, 7]
    target_indices = [i for i, entry in enumerate(result_list) if entry['class'] in target_classes]
    
    # For each Notes entry, find all nearby targets on same line and merge
    for note_idx in notes_indices:
        note_entry = result_list[note_idx]
        
        if not target_indices:
            continue
        
        # Find all targets that are vertically aligned and horizontally close
        targets_to_merge = []
        
        for target_idx in target_indices:
            # Skip if this target was already merged
            if target_idx in indices_to_remove:
                continue
            
            target_entry = result_list[target_idx]
            
            # Check if they are on the same line (vertically aligned)
            v_aligned = are_vertically_aligned(note_entry['bbox'], target_entry['bbox'])
            h_gap = get_horizontal_gap(note_entry['bbox'], target_entry['bbox'])
                        
            if v_aligned and h_gap <= horizontal_gap_threshold:
                targets_to_merge.append((target_idx, h_gap))
        
        # Merge all found targets with the note
        if targets_to_merge:
            # Sort by horizontal distance (closest first)
            targets_to_merge.sort(key=lambda x: x[1])
            
            merged_bbox = note_entry['bbox'].copy()
            merged_classes = [note_entry['class']]
            
            for target_idx, h_gap in targets_to_merge:
                target_entry = result_list[target_idx]
                merged_bbox = merge_bboxes(merged_bbox, target_entry['bbox'])
                merged_classes.append(target_entry['class'])
                
                # Mark this target for removal
                indices_to_remove.add(target_idx)
                
              
            
            # Update the Note entry's bbox with merged coordinates
            result_list[note_idx]['bbox'] = merged_bbox
           
    
    # Remove merged targets (iterate in reverse to avoid index shifting)
    for idx in sorted(indices_to_remove, reverse=True):
        removed_class = result_list[idx]['class']
        result_list.pop(idx)
    
    return result_list

def mergeSC(bbox_list, max_distance_threshold=1000):
        
    def calculate_distance(bbox1, bbox2):
        # Calculate centers
        center1_x = (bbox1[0] + bbox1[2]) / 2
        center1_y = (bbox1[1] + bbox1[3]) / 2
        center2_x = (bbox2[0] + bbox2[2]) / 2
        center2_y = (bbox2[1] + bbox2[3]) / 2
        
        # Calculate Euclidean distance
        distance = math.sqrt((center1_x - center2_x)**2 + (center1_y - center2_y)**2)
        return distance
    
    def merge_bboxes(bbox1, bbox2):
        min_x = min(bbox1[0], bbox2[0])
        min_y = min(bbox1[1], bbox2[1])
        max_x = max(bbox1[2], bbox2[2])
        max_y = max(bbox1[3], bbox2[3])
        return [min_x, min_y, max_x, max_y]
    
    # Create a copy of the list to avoid modifying the original
    result_list = [entry.copy() for entry in bbox_list]
    for entry in result_list:
        entry['bbox'] = entry['bbox'].copy()
    
    # Find all Special_Characteristics entries (class 4)
    special_chars_indices = []
    for i, entry in enumerate(result_list):
        if entry['class'] == 4:
            special_chars_indices.append(i)
    
    # Find all target classes (Dimensions=0, GDnT=1, Notes=2)
    target_indices = []
    for i, entry in enumerate(result_list):
        if entry['class'] in [0, 1, 2]:  # Dimensions, GDnT, Notes
            target_indices.append(i)
    
    # Track indices to remove
    indices_to_remove = set()
    
    # For each Special_Characteristics entry, find closest target and merge
    for sc_idx in special_chars_indices:
        sc_entry = result_list[sc_idx]
        
        if not target_indices:
            continue  # No target entries to merge with
        
        # Find the closest target entry within threshold
        min_distance = float('inf')
        closest_target_idx = None
        
        for target_idx in target_indices:
            # Skip if this target was already merged/removed
            if target_idx in indices_to_remove:
                continue
                
            target_entry = result_list[target_idx]
            distance = calculate_distance(sc_entry['bbox'], target_entry['bbox'])
            
            # Only consider targets within the threshold distance
            if distance <= max_distance_threshold and distance < min_distance:
                min_distance = distance
                closest_target_idx = target_idx
        
        # Merge the bounding boxes only if a target was found within threshold
        if closest_target_idx is not None:
            closest_entry = result_list[closest_target_idx]
            merged_bbox = merge_bboxes(sc_entry['bbox'], closest_entry['bbox'])
            
            # Update the target entry's bbox with merged coordinates
            result_list[closest_target_idx]['bbox'] = merged_bbox
            
            # Mark the Special_Characteristics for removal
            indices_to_remove.add(sc_idx)    
    # Remove merged Special_Characteristics (iterate in reverse to avoid index shifting)
    for idx in sorted(indices_to_remove, reverse=True):
        result_list.pop(idx)
    
    return result_list


def detectViews(imagePath: str, outputDir: str):

    #imagePath = extractBetweenContours(imagePath, outputDir)
    image = cv2.imread(imagePath)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    inverted = cv2.bitwise_not(gray)

    _, binary_test = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    content_density = cv2.countNonZero(binary_test) / (image.shape[0] * image.shape[1])

    # Adapt kernel and iterations based on density
    if content_density > 0.15:  # Dense drawing
        kernel = np.ones((3, 20), np.uint8)
        iterations = 1
    elif content_density > 0.08:  # Medium density
        kernel = np.ones((4, 35), np.uint8)
        iterations = 2
    else:  # Sparse drawing
        kernel = np.ones((40, 50), np.uint8)
        iterations = 3

    dilated = cv2.dilate(inverted, kernel, iterations=iterations)
    thickened = cv2.bitwise_not(dilated)

    _, binary = cv2.threshold(thickened, 200, 255, cv2.THRESH_BINARY_INV)

    merge_kernel = np.ones((15, 15), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, merge_kernel)
    binary = cv2.bitwise_not(binary)
    contours, hierarchy = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    parentCountersIndex = []
    if hierarchy is not None and len(contours) > 0:
        for i, h in enumerate(hierarchy[0]):
            parent = h[3]
            parent_count = 0

            while parent != -1:
                parent_count += 1
                parent = hierarchy[0][parent][3]

            if parent_count <= 1:
                parentCountersIndex.append(i)

    parent_contours = [item for i, item in enumerate(contours) if i in parentCountersIndex]

    image_height, image_width = image.shape[:2]
    total_image_area = image_height * image_width

    MIN_AREA = 0.003 * total_image_area
    MAX_AREA = 0.7 * total_image_area

    _, ink_mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink_mask = cv2.morphologyEx(ink_mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    filtered_contours = []
    debug_info = []

    for idx, cnt in enumerate(parent_contours):
        area = cv2.contourArea(cnt)
        if not (MIN_AREA <= area <= MAX_AREA):
            continue

        mask = np.zeros_like(ink_mask, dtype=np.uint8)
        cv2.drawContours(mask, [cnt], -1, 255, -1)

        erode_kernel = np.ones((5, 5), np.uint8)
        inner_mask = cv2.erode(mask, erode_kernel, iterations=2)

        if cv2.countNonZero(inner_mask) == 0:
            inner_mask = mask

        inside = cv2.bitwise_and(ink_mask, inner_mask)
        ink_pixels = cv2.countNonZero(inside)
        area_pixels = cv2.countNonZero(inner_mask)

        if len(debug_info) < 10:
            debug_info.append((idx, area, area_pixels, ink_pixels))

        if ink_pixels == 0:
            continue

        density = ink_pixels / (area_pixels + 1e-8)
        if density < 0.005:
            continue

        filtered_contours.append(cnt)

    # PNG / sparse binarization can yield no valid regions — use full image as one view.
    if not filtered_contours:
        ih, iw = image.shape[:2]
        synthetic = np.array(
            [[[0, 0]], [[iw - 1, 0]], [[iw - 1, ih - 1]], [[0, ih - 1]]],
            dtype=np.int32,
        )
        filtered_contours = [synthetic]
        Logger.info("detectViews: no sub-regions found; using full image as single view")

    def sort_contours(contours, y_threshold=300):
        # Step 1: Extract bounding boxes
        boxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            boxes.append((cnt, x, y, w, h))

        # Step 2: Sort by y initially
        boxes.sort(key=lambda b: b[2])  # sort by y

        # Step 3: Group contours into rows
        rows = []
        current_row = [boxes[0]]

        for b in boxes[1:]:
            _, x, y, w, h = b
            prev_y = current_row[-1][2]

            # If y difference is small → same row
            if abs(y - prev_y) < y_threshold:
                current_row.append(b)
            else:
                rows.append(current_row)
                current_row = [b]

        rows.append(current_row)

        # Step 4: Sort each row left → right by x
        for r in rows:
            r.sort(key=lambda b: b[1])  # sort by x

        # Step 5: Flatten sorted rows into single list
        sorted_contours = []
        for r in rows:
            for b in r:
                sorted_contours.append(b[0])

        return sorted_contours

    sorted_filtered_contours = sort_contours(filtered_contours)
    outputDir = outputDir + "/detectedViews"
    os.makedirs(outputDir, exist_ok=True)

    # Store coordinate mappings for all views
    coordinate_mappings = {}
    
    # Minimum dimensions for output images
    MIN_OUTPUT_DIM = 5000

    for idx, cnt in enumerate(sorted_filtered_contours):
        # Get bounding rectangle
        x, y, w, h = cv2.boundingRect(cnt)
        
        # Add some padding
        padding = 20
        x_pad = max(0, x - padding)
        y_pad = max(0, y - padding)
        w_pad = min(image_width - x_pad, w + 2 * padding)
        h_pad = min(image_height - y_pad, h + 2 * padding)
        
        # Create white background
        white_bg = np.ones((h_pad, w_pad, 3), dtype=np.uint8) * 255
        
        # Create mask for this specific contour
        mask = np.zeros((image_height, image_width), dtype=np.uint8)
        cv2.drawContours(mask, [cnt], -1, 255, -1)
        
        # Crop the mask to the bounding box
        mask_crop = mask[y_pad:y_pad + h_pad, x_pad:x_pad + w_pad]
        
        # Crop the original image to the bounding box
        cropped = image[y_pad:y_pad + h_pad, x_pad:x_pad + w_pad]
        
        # Apply mask: copy only the contour area to white background
        white_bg[mask_crop == 255] = cropped[mask_crop == 255]
        
        # Calculate padding needed to reach minimum dimensions
        current_h, current_w = white_bg.shape[:2]
        
        # Calculate total padding needed
        pad_w = max(0, MIN_OUTPUT_DIM - current_w)
        pad_h = max(0, MIN_OUTPUT_DIM - current_h)
        
        # Distribute padding evenly on both sides
        pad_left = pad_w // 2
        pad_right = pad_w - pad_left
        pad_top = pad_h // 2
        pad_bottom = pad_h - pad_top
        
        # Add white padding to reach minimum dimensions
        if pad_w > 0 or pad_h > 0:
            white_bg = cv2.copyMakeBorder(
                white_bg,
                pad_top, pad_bottom, pad_left, pad_right,
                cv2.BORDER_CONSTANT,
                value=[255, 255, 255]
            )
        
        # Store the coordinate mapping (without the additional padding)
        view_name = f"view_{idx + 1}"
        coordinate_mappings[view_name] = {
            "offset_x": x_pad,
            "offset_y": y_pad,
            "crop_width": w_pad,
            "crop_height": h_pad,
            "original_image_width": image_width,
            "original_image_height": image_height,
            "padding_left": pad_left,
            "padding_top": pad_top,
            "padding_right": pad_right,
            "padding_bottom": pad_bottom,
            "padded_image_width": white_bg.shape[1],
            "padded_image_height": white_bg.shape[0]
        }
        
        # Save the extracted contour
        output_path = os.path.join(outputDir, f"{view_name}.png")
        cv2.imwrite(output_path, white_bg)
    
    # Save coordinate mappings to JSON file
    mapping_file = os.path.join(outputDir, "coordinate_mappings.json")
    with open(mapping_file, 'w') as f:
        json.dump(coordinate_mappings, f, indent=4)    
    return outputDir, mapping_file


def RunInference(weights, image_path, imgsz=1024, conf_thres=0.5, iou_thres=0.45, 
                 device='cpu', CustomNms_threshold=0.6, bbox_padding=40, class_conf_thres=None):
    
    # `weights` kept for call-site compatibility; global model path is resolve_autoballoon_weights_path().
    model = get_yolo_model()
    if class_conf_thres is None:
        class_conf_thres = {"Special_Characteristics": 0.10, "Surface_Finish_Symbols": 0.14}
    # Check if image exists
    if not os.path.exists(image_path):
        raise ValueError(f"Could not load image from {image_path}")
    
    # Get original image dimensions for reference
    orig_img = cv2.imread(image_path)
    if orig_img is None:
        raise ValueError(f"Could not read image: {image_path}")
    orig_h, orig_w = orig_img.shape[:2]
    Logger.debug(f"Original image size: {orig_w}x{orig_h}")
    
    # Run YOLO below post-filter thresholds so marginal boxes are not dropped before the loop.
    # (Previously min(conf_thres, 0.1) removed e.g. 0.11 conf when conf_thres=0.12.)
    base_conf = 0.01 if class_conf_thres else conf_thres

    results = model(
        image_path,
        imgsz=imgsz,
        conf=base_conf,
        iou=iou_thres,
        device=device,
        verbose=False
    )
    
    # Extract detections
    detections = []
    result = results[0]
    
    if result.boxes is not None:
        boxes = result.boxes.xyxy.cpu().numpy()
        confidences = result.boxes.conf.cpu().numpy()
        class_ids = result.boxes.cls.cpu().numpy().astype(int)
        class_names = result.names
        
        for i in range(len(boxes)):
            bbox = [int(boxes[i][0]), int(boxes[i][1]), int(boxes[i][2]), int(boxes[i][3])]
            bbox[0] = max(0, bbox[0] - bbox_padding)  # x1
            bbox[1] = max(0, bbox[1] - bbox_padding)  # y1
            bbox[2] = min(orig_w, bbox[2] + bbox_padding)  # x2
            bbox[3] = min(orig_h, bbox[3] + bbox_padding)  # y2 (was missing — caused bad crops)
            class_id = class_ids[i]
            confidence = confidences[i]
            class_name = class_names[class_id]
            
            # Apply class-specific confidence threshold
            if class_conf_thres and class_name in class_conf_thres:
                threshold = class_conf_thres[class_name]
            else:
                threshold = conf_thres  # Use default threshold
            
            # Skip detection if below threshold
            if confidence < threshold:
                continue
            
            detections.append({
                "bbox": bbox,
                "class": class_id,
                "conf": float(confidence),
                "class_name": class_name
            })
    
    Logger.debug(f"Total detections before custom NMS: {len(detections)}")
    
    detections = CustomNms(detections, CustomNms_threshold)
   
    detections = mergeSC(detections)
    detections = mergeNotes(detections)
    
    Logger.debug(f"Total detections after custom NMS: {len(detections)}")
    return detections


def yolo_raw_detections_to_annotation_list(raw_dets):
    """
    Build DrawingAnnotations-shaped entries from RunInference() output when the
    multi-view crop pipeline produced no rows (common on large PNGs).
    Balloon ids follow reading order: top→bottom, then left→right (bbox center).
    """
    dets = [d for d in (raw_dets or []) if d.get("bbox") and len(d["bbox"]) >= 4]
    dets = sort_detections_tblr(dets)
    out = []
    for idx, d in enumerate(dets, start=1):
        bbox = d.get("bbox")
        cls_name = d.get("class_name") or "Dimensions"
        x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]
        out.append(
            {
                "AnnotationType": cls_name,
                "BBox": [int(x1), int(y1), int(x2), int(y2)],
                "id": idx,
            }
        )
    return out


def cluster_and_sort_detections(detections, image_shape):
    """
    Cluster detections spatially (DBSCAN),
    then sort inside each cluster (top->bottom, left->right),
    then sort clusters themselves by the TOP-LEFT corner
    of their cluster bounding box.
    """

    if not detections or len(detections) == 1:
        return detections

    H, W = image_shape[:2]
    
    # --- STEP 1: Prepare center points ---
    centers = []
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        centers.append([cx, cy])
    centers = np.array(centers)

    # --- STEP 2: DBSCAN clustering ---
    diag = np.sqrt(W*W + H*H)
    eps_distance = 0.08 * diag   # 8% of diagonal

    clustering = DBSCAN(eps=eps_distance, min_samples=1).fit(centers)
    labels = clustering.labels_

    # Build clusters
    clusters = {}
    for label, det, center in zip(labels, detections, centers):
        clusters.setdefault(label, []).append({
            "detection": det,
            "cx": center[0],
            "cy": center[1],
            "bbox": det["bbox"]
        })

    # --- STEP 3: Sort inside clusters ---
    sorted_clusters = []
    for label, items in clusters.items():
        # Sort inside cluster (top-left first)
        items.sort(key=lambda d: (d["cy"], d["cx"]))

        # Cluster bounding box (top-left corner)
        min_x = min(d["bbox"][0] for d in items)
        min_y = min(d["bbox"][1] for d in items)

        sorted_clusters.append({
            "cluster_x": min_x,   # top-left corner X
            "cluster_y": min_y,   # top-left corner Y
            "items": [i["detection"] for i in items]
        })

    # --- STEP 4: Sort clusters by top-left bounding box ---
    sorted_clusters.sort(key=lambda c: (c["cluster_y"], c["cluster_x"]))

    # --- STEP 5: Merge results ---
    final_sorted = []
    for c in sorted_clusters:
        final_sorted.extend(c["items"])

    return final_sorted





def _adaptive_eps(centers, diag, k=4, scale=1.0, min_eps=2.0):
    """
    Compute eps from median k-distance * scale.
    - centers: Nx2 array
    - diag: image diagonal (for fallback if centers degenerate)
    - k: number of neighbors for k-distance
    - scale: multiplier (tune <1 to make clusters smaller)
    - min_eps: lower-bound in pixels
    """
    if len(centers) <= k:
        # fallback to a small fraction of diagonal
        return max(min_eps, 0.03 * diag * scale)
    nbrs = NearestNeighbors(n_neighbors=min(len(centers), k+1)).fit(centers)
    dists, _ = nbrs.kneighbors(centers)
    # distances include self=0 at column 0; take column k (k-th neighbor)
    kth = dists[:, -1]
    med = float(np.median(kth))
    return max(min_eps, med * scale)



def sort_detections_gridwise(detections, image_shape, grid_rows=4, grid_cols=6):
    """
    Sort detections based on their bbox center using an imaginary grid system.
    Ordering:
        - Grid-by-grid: top-left → top-right → next row → bottom
        - Inside each grid: sort by row then by column (top-left → top-right)
    """

    if not detections or len(detections) == 1:
        return detections

    img_h, img_w = image_shape[:2]

    # Grid cell size
    cell_w = img_w / grid_cols
    cell_h = img_h / grid_rows

    enriched = []

    # Compute center & assign grid cell
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        # Determine grid cell index
        grid_x = int(cx // cell_w)
        grid_y = int(cy // cell_h)

        # Clamp boundaries (avoid index overflow on edge cases)
        grid_x = min(grid_x, grid_cols - 1)
        grid_y = min(grid_y, grid_rows - 1)

        enriched.append({
            "detection": det,
            "cx": cx,
            "cy": cy,
            "grid_x": grid_x,
            "grid_y": grid_y
        })

    # Sorting priority:
    # 1. grid_y (top row first)
    # 2. grid_x (left grid first)
    # 3. cy (inside grid, top items first)
    # 4. cx (inside grid, left items first)
    enriched.sort(key=lambda d: (
        d["grid_y"],
        d["grid_x"],
        d["cy"],
        d["cx"]
    ))

    sorted_detections = [d["detection"] for d in enriched]
    return sorted_detections

def sort_detections_tblr(detections):
    """
    Sort detections top-to-bottom, then left-to-right using bbox top-left (y1, x1).

    Reading order on drawings: upper rows first; within a row, left to right.
    """

    if not detections or len(detections) == 1:
        return detections

    enriched = []

    for det in detections:
        x1, y1, x2, y2 = det["bbox"]

        enriched.append({
            "detection": det,
            "y1": float(y1),
            "x1": float(x1),
        })

    enriched.sort(key=lambda d: (d["y1"], d["x1"]))

    return [d["detection"] for d in enriched]


def sort_detections_clockwise(detections, image_shape):

    if not detections or len(detections) == 1:
        return detections

    # 1️⃣ Compute geometric center
    all_cx = [(d["bbox"][0] + d["bbox"][2]) / 2 for d in detections]
    all_cy = [(d["bbox"][1] + d["bbox"][3]) / 2 for d in detections]
    center_x = sum(all_cx) / len(all_cx)
    center_y = sum(all_cy) / len(all_cy)

    # 2️⃣ Compute angles (0° = right, increases clockwise)
    detection_info = []
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        dx = cx - center_x
        dy = cy - center_y

        # atan2 gives angle from x-axis; adjust to 0° at left (9 o'clock)
        angle = (math.degrees(math.atan2(dy, dx)) + 360) % 360
        # Shift so 0° corresponds to leftmost direction
        angle = (angle + 180) % 360

        detection_info.append({
            "detection": det,
            "angle": angle,
            "cx": cx,
            "cy": cy
        })

    # 3️⃣ Sort detections clockwise
    detection_info.sort(key=lambda d: d["angle"])

    # 4️⃣ Return sorted detections
    sorted_detections = [d["detection"] for d in detection_info]

    return sorted_detections


def calculate_iou(bbox1, bbox2):
    x1_1, y1_1, x2_1, y2_1 = bbox1
    x1_2, y1_2, x2_2, y2_2 = bbox2
    
    # Calculate intersection area
    x_left = max(x1_1, x1_2)
    y_top = max(y1_1, y1_2)
    x_right = min(x2_1, x2_2)
    y_bottom = min(y2_1, y2_2)
    
    if x_right < x_left or y_bottom < y_top:
        return 0.0
    
    intersection_area = (x_right - x_left) * (y_bottom - y_top)
    
    # Calculate union area
    bbox1_area = (x2_1 - x1_1) * (y2_1 - y1_1)
    bbox2_area = (x2_2 - x1_2) * (y2_2 - y1_2)
    union_area = bbox1_area + bbox2_area - intersection_area
    
    if union_area == 0:
        return 0.0
    
    iou = intersection_area / union_area
    return iou

def bbox_overlaps_with_region(bbox, region_x1, region_y1, region_x2, region_y2):
    det_x1, det_y1, det_x2, det_y2 = bbox    
    # Check if there's any overlap
    if det_x2 < region_x1 or det_x1 > region_x2:
        return False
    if det_y2 < region_y1 or det_y1 > region_y2:
        return False
    
    return True

def ProcessMultipleViews(views_directory, coordinate_mappings_path, weights, 
                        original_image_path, output_dir, imgsz=1024, 
                        conf_thres=0.15, iou_thres=0.45, device='cpu', 
                        CustomNms_threshold=0.6, class_conf_thres=None,
                        inter_view_iou_threshold=0.8):  
    
    # Load coordinate mappings
    with open(coordinate_mappings_path, 'r') as f:
        coordinate_mappings = json.load(f)
    
    # Get all image files from views directory
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}
    view_files = []
    
    for file in os.listdir(views_directory):
        if Path(file).suffix.lower() in image_extensions:
            view_files.append(file)
    
    # Sort view files to maintain order (view_1, view_2, etc.)
    view_files.sort(key=lambda f: int(Path(f).stem.split('_')[-1]))

    
    Logger.debug(f"Found {len(view_files)} view images to process")
    
    # Store all dimension crops and metadata
    all_dimension_crops = []  # List of (crop_image, detection_info) tuples
    all_detections = []
    detection_id_counter = 1
    
    # NEW: Store accumulated detections from all previous views
    accumulated_detections = []
    
    # Load original image once for coordinate reference
    original_img = cv2.imread(original_image_path)
    if original_img is None:
        Logger.debug(f"Could not load original image from {original_image_path}")
        return None, []
    
    # Track which regions of the original image are covered by views
    view_regions = []
    
    # Process each view image
    for view_file in view_files:
        view_path = os.path.join(views_directory, view_file)
        view_name = Path(view_file).stem  # e.g., "view_1"
        
        Logger.debug(f"\nProcessing {view_name}...")
        #print(f"\nProcessing {view_name}...")
        
        # Check if this view has coordinate mapping
        if view_name not in coordinate_mappings:
            Logger.debug(f"⚠️  No coordinate mapping found for {view_name}, skipping...")
            continue
        
        # Store view region for later filtering
        mapping = coordinate_mappings[view_name]
        view_regions.append({
            "name": view_name,
            "offset_x": mapping["offset_x"],
            "offset_y": mapping["offset_y"],
            "pad_left": mapping.get("padding_left", 0),
            "pad_top": mapping.get("padding_top", 0),
            "width": mapping["crop_width"],
            "height": mapping["crop_height"]
        })
        
        # Run YOLO inference on this view
        try:
            view_detections = RunInference(
                weights=weights,
                image_path=view_path,
                imgsz=imgsz,
                conf_thres=conf_thres,
                iou_thres=iou_thres,
                device=device,
                CustomNms_threshold=CustomNms_threshold,
                class_conf_thres=class_conf_thres
            )
            Logger.debug(f"Found {len(view_detections)} detections in {view_name}")
            
            # Transform each detection back to original image coordinates FIRST
            offset_x = mapping["offset_x"]
            offset_y = mapping["offset_y"]
            pad_left = mapping.get("padding_left", 0)
            pad_top = mapping.get("padding_top", 0)
            
            transformed_detections = []
            for detection in view_detections:
                # Get bbox in view coordinates (padded image)
                x1, y1, x2, y2 = detection["bbox"]
                
                # First, remove the padding to get coordinates in the cropped view
                x1_no_pad = x1 - pad_left
                y1_no_pad = y1 - pad_top
                x2_no_pad = x2 - pad_left
                y2_no_pad = y2 - pad_top
                
                # Then, transform to original image coordinates
                original_x1 = x1_no_pad + offset_x
                original_y1 = y1_no_pad + offset_y
                original_x2 = x2_no_pad + offset_x
                original_y2 = y2_no_pad + offset_y
                
                # Create transformed detection
                transformed_detection = {
                    "bbox": [original_x1, original_y1, original_x2, original_y2],
                    "class": detection["class"],
                    "conf": detection["conf"],
                    "class_name": detection["class_name"],
                    "source_view": view_name
                }
                transformed_detections.append(transformed_detection)
            
            # Sort by reading order: top→bottom, then left→right (bbox center).
            transformed_detections = sort_detections_tblr(transformed_detections)
            
            # ========== NEW: IoU-based filtering against previous views ==========
            Logger.debug(f"\n--- IoU Filtering for {view_name} ---")
            Logger.debug(f"Checking {len(transformed_detections)} detections against {len(accumulated_detections)} accumulated detections")
            
            filtered_detections = []
            discarded_count = 0
            
            for detection in transformed_detections:
                is_duplicate = False
                
                # Check against all accumulated detections from previous views
                for prev_detection in accumulated_detections:
                    # Only compare detections of the same class
                    if detection["class_name"] == prev_detection["class_name"]:
                        iou = calculate_iou(detection["bbox"], prev_detection["bbox"])
                        
                        if iou > inter_view_iou_threshold:
                            is_duplicate = True
                            discarded_count += 1
                            Logger.debug(f"  ❌ Discarded {detection['class_name']} (IoU={iou:.3f} with detection from {prev_detection['source_view']})")
                            break
                
                # If not a duplicate, keep it
                if not is_duplicate:
                    filtered_detections.append(detection)
            
            Logger.debug(f"Kept {len(filtered_detections)} detections, discarded {discarded_count} duplicates")
            Logger.debug(f"{'='*60}\n")
            
            # Replace transformed_detections with filtered ones
            transformed_detections = filtered_detections
            # ========== END NEW CODE ==========
            
            target_classes = YOLO_TARGET_CLASSES_FOR_CROPS
            
            # Process sorted and filtered detections
            for detection in transformed_detections:
                # Add detection ID
                detection["detection_id"] = detection_id_counter
                detection["id"] = detection_id_counter
                
                all_detections.append(detection)
                
                # NEW: Add to accumulated detections for future view comparisons
                accumulated_detections.append(detection)
                
                # If it's a target class, extract the crop immediately
                if detection["class_name"] in target_classes:
                    # Get transformed bbox
                    original_x1, original_y1, original_x2, original_y2 = detection["bbox"]
                    # Ensure coordinates are within image bounds
                    h, w = original_img.shape[:2]
                    x1_clip = max(0, int(original_x1))
                    y1_clip = max(0, int(original_y1))
                    x2_clip = min(w, int(original_x2))
                    y2_clip = min(h, int(original_y2))
                    
                    # Extract crop from original image
                    crop = original_img[y1_clip:y2_clip, x1_clip:x2_clip].copy()
                    
                    if crop.size > 0:
                        # Store crop with its metadata
                        crop_info = {
                            "crop": crop,
                            "detection_id": detection_id_counter,
                            "class_name": detection["class_name"],
                            "source_view": view_name,
                            "bbox": detection["bbox"]
                        }
                        all_dimension_crops.append(crop_info)                                
                        detection_id_counter += 1
                
        except Exception as e:
            Logger.debug(f"❌ Error processing {view_name}: {str(e)}")
            continue
    
    # ========== Run inference on full original image ==========
    Logger.debug(f"\n{'='*60}")
    Logger.debug("Running inference on full original image to catch missed elements...")
    Logger.debug(f"{'='*60}\n")
    #print("calling inference on complete image")
    try:
        full_image_detections = RunInference(
            weights=weights,
            image_path=original_image_path,
            imgsz=imgsz,
            conf_thres=conf_thres,
            iou_thres=iou_thres,
            device=device,
            CustomNms_threshold=CustomNms_threshold,
            class_conf_thres=class_conf_thres
        )
        Logger.debug(f"Found {len(full_image_detections)} detections in full image")
        #print("called successfully")
        # Helper function to check if a bbox overlaps with a region
        
        
        # Filter out detections that overlap with any view region
        non_view_detections = []
        
        for detection in full_image_detections:
            # Check if this detection overlaps with any view region
            is_in_view = False
            
            for view_region in view_regions:
                view_x1 = view_region["offset_x"]
                view_y1 = view_region["offset_y"]
                view_x2 = view_x1 + view_region["width"]
                view_y2 = view_y1 + view_region["height"]
                
                if bbox_overlaps_with_region(detection["bbox"], view_x1, view_y1, view_x2, view_y2):
                    is_in_view = True
                    Logger.debug(f"Skipping detection in view region: {detection['class_name']} in {view_region['name']}")
                    break
            
            # If not overlapping with any view, add it
            if not is_in_view:
                detection["source_view"] = "full_image"
                non_view_detections.append(detection)
        
        Logger.debug(f"Found {len(non_view_detections)} detections outside view regions")
        
        non_view_detections = sort_detections_tblr(non_view_detections)
        
        target_classes = YOLO_TARGET_CLASSES_FOR_CROPS

        # Process non-view detections
        for detection in non_view_detections:
            detection["detection_id"] = detection_id_counter
            detection["id"] = detection_id_counter
            
            all_detections.append(detection)
            
            # If it's a target class, extract the crop
            if detection["class_name"] in target_classes:
                x1, y1, x2, y2 = detection["bbox"]
                h, w = original_img.shape[:2]
                x1_clip = max(0, int(x1))
                y1_clip = max(0, int(y1))
                x2_clip = min(w, int(x2))
                y2_clip = min(h, int(y2))
                
                crop = original_img[y1_clip:y2_clip, x1_clip:x2_clip].copy()
                
                if crop.size > 0:
                    crop_info = {
                        "crop": crop,
                        "detection_id": detection_id_counter,
                        "class_name": detection["class_name"],
                        "source_view": "full_image",
                        "bbox": detection["bbox"]
                    }
                    all_dimension_crops.append(crop_info)
            
                    detection_id_counter += 1
                    
    except Exception as e:
        Logger.debug(f"❌ Error processing full image: {str(e)}")
    
    # ========== END FULL IMAGE PROCESSING ==========
    
    Logger.debug(f"\n{'='*60}")
    Logger.debug(f"Total detections across all views: {len(all_detections)}")
    Logger.debug(f"Total dimension crops extracted: {len(all_dimension_crops)}")
    Logger.debug(f"{'='*60}\n")
    
    if not all_dimension_crops:
        Logger.debug("No dimension crops found in any view")
        return None, []
    
    # Now create the final dimension image from all collected crops
    #print("trying to genrate dimension Image")
    dimension_image_path = CreateDimensionImageFromCrops(
        crops_info=all_dimension_crops,
        original_image_path=original_image_path,
        output_dir=output_dir
    )
    
    # Save metadata about detections including source views
    metadata = {
        "total_views_processed": len(view_files),
        "total_detections": len(all_detections),
        "total_dimension_crops": len(all_dimension_crops),
        "detections_by_view": {}
    }
    
    # Group detections by source view
    for detection in all_detections:
        view = detection["source_view"]
        if view not in metadata["detections_by_view"]:
            metadata["detections_by_view"][view] = []
        metadata["detections_by_view"][view].append({
            "detection_id": detection["detection_id"],
            "class_name": detection["class_name"],
            "confidence": detection["conf"],
            "bbox": detection["bbox"]
        })
    
    # Save metadata to JSON
    metadata_path = os.path.join(output_dir, "detections_metadata.json")
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=4)
    
    Logger.debug(f"Saved detection metadata to: {metadata_path}")
    
    # Prepare final detections list in the required format
    final_detections = []
    for crop_info in all_dimension_crops:
        final_detections.append({
            "AnnotationType": crop_info["class_name"],
            "BBox": crop_info["bbox"],
            "id": crop_info["detection_id"]
        })
    
    return dimension_image_path, final_detections


def CreateDimensionImageFromCrops(crops_info, original_image_path, output_dir,
                                  regions_per_row=5, quality_enhancement=False,
                                  min_scale_factor=2.0, max_scale_factor=8.0):
    
    
    if not crops_info:
        Logger.debug("No crops provided")
        return None
    
    Logger.debug(f"Creating dimension image from {len(crops_info)} crops...")
    
    # CRITICAL: If too many crops, reduce regions_per_row to avoid huge images
    if len(crops_info) > 100:
        regions_per_row = min(regions_per_row, 3)
        Logger.debug(f"⚠️ Many crops detected, reducing regions_per_row to {regions_per_row}")
    
    def enhance_crop_quality(crop):
        if not quality_enhancement or crop.size == 0:
            return crop
        
        try:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray)
            denoised = cv2.fastNlMeansDenoising(enhanced)
            kernel = np.array([[-1,-1,-1], [-1, 9,-1], [-1,-1,-1]])
            sharpened = cv2.filter2D(denoised, -1, kernel)
            result = cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)
            # Clean up intermediate arrays
            del gray, enhanced, denoised, sharpened
            return result
        except:
            return crop
    
    def calculate_adaptive_scale(crop_size, reference_sizes, min_scale=2.0, max_scale=8.0):
        crop_area = crop_size[0] * crop_size[1]
        
        if not reference_sizes:
            return min_scale
        
        areas = [size[0] * size[1] for size in reference_sizes]
        median_area = sorted(areas)[len(areas) // 2]
        
        if crop_area > 0:
            area_ratio = median_area / crop_area
            scale_factor = min_scale + (max_scale - min_scale) * min(area_ratio / 4.0, 1.0)
            return max(min_scale, min(max_scale, scale_factor))
        
        return min_scale
    
    def adaptive_resize_crop(crop, scale_factor):
        if scale_factor <= 1.0:
            return crop
            
        height, width = crop.shape[:2]
        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)
        
        # LIMIT: Cap maximum dimensions to prevent massive memory usage
        max_dimension = 2000
        if new_width > max_dimension or new_height > max_dimension:
            scale_down = min(max_dimension / new_width, max_dimension / new_height)
            new_width = int(new_width * scale_down)
            new_height = int(new_height * scale_down)
            Logger.debug(f"⚠️ Capping resize to {new_width}x{new_height}")
        
        resized = cv2.resize(crop, (new_width, new_height), interpolation=cv2.INTER_CUBIC)
        return resized
    
    def calculate_fixed_font_params(target_cell_height, target_cell_width):
        min_dimension = min(target_cell_height, target_cell_width)
        font_scale = max(1.5, min_dimension / 150.0)
        thickness = max(3, int(font_scale * 2.0))
        label_height = max(60, int(min_dimension * 0.2))
        return font_scale, thickness, label_height
    
    def make_adaptive_size(images, target_size, bg_color=255):
        h_tgt, w_tgt = target_size
        padded_imgs = []
        
        for img in images:
            h, w = img.shape[:2]
            
            if h > h_tgt or w > w_tgt:
                scale = min(h_tgt / h, w_tgt / w)
                new_w, new_h = int(w * scale), int(h * scale)
                resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
                h, w = new_h, new_w
            else:
                resized = img
            
            pad_top = (h_tgt - h) // 2
            pad_bottom = h_tgt - h - pad_top
            pad_left = (w_tgt - w) // 2
            pad_right = w_tgt - w - pad_left

            if len(resized.shape) == 3:
                padded = cv2.copyMakeBorder(resized, pad_top, pad_bottom, pad_left, pad_right,
                                          cv2.BORDER_CONSTANT, value=(bg_color, bg_color, bg_color))
            else:
                padded = cv2.copyMakeBorder(resized, pad_top, pad_bottom, pad_left, pad_right,
                                          cv2.BORDER_CONSTANT, value=bg_color)
            padded_imgs.append(padded)
            
            # Clean up
            if resized is not img:
                del resized
        
        return padded_imgs
    
    # Calculate reference sizes
    reference_sizes = [(info["crop"].shape[:2]) for info in crops_info]
    
    # CRITICAL: Process crops in smaller batches to avoid memory spike
    valid_imgs = []
    font = cv2.FONT_HERSHEY_SIMPLEX
    batch_size = 20
    
    Logger.debug(f"Processing {len(crops_info)} crops in batches of {batch_size}...")
    
    for batch_idx in range(0, len(crops_info), batch_size):
        batch = crops_info[batch_idx:batch_idx + batch_size]
        
        for crop_info in batch:
            crop = crop_info["crop"]
            label_num = crop_info["detection_id"]
            
            # Calculate adaptive scale factor
            scale_factor = calculate_adaptive_scale(crop.shape[:2], reference_sizes, 
                                                  min_scale_factor, max_scale_factor)
            
            # Apply adaptive scaling
            scaled_crop = adaptive_resize_crop(crop, scale_factor)
            
            # Enhance quality after scaling
            enhanced_crop = enhance_crop_quality(scaled_crop)
            
            # Clean up scaled_crop if it's different from crop
            if scaled_crop is not crop:
                del scaled_crop
            
            # Check if rotation is needed
            height, width = enhanced_crop.shape[:2]
            should_rotate = height > 4.5 * width
            
            if should_rotate:
                rotated_crop = cv2.rotate(enhanced_crop, cv2.ROTATE_90_CLOCKWISE)
                valid_imgs.append((rotated_crop, label_num, scale_factor))
                del enhanced_crop
            else:
                valid_imgs.append((enhanced_crop, label_num, scale_factor))
        
        # Force cleanup after each batch
        if (batch_idx + batch_size) % 60 == 0:
            gc.collect()
            Logger.debug(f"Processed {min(batch_idx + batch_size, len(crops_info))}/{len(crops_info)} crops")
    
    if not valid_imgs:
        Logger.debug("No valid crops to process")
        return None
    
    Logger.debug(f"✓ Processed all {len(valid_imgs)} crops")
    
    # Calculate grid cell size
    heights = [img[0].shape[0] for img in valid_imgs]
    widths = [img[0].shape[1] for img in valid_imgs]
    
    target_height = int(max(heights))
    target_width = int(np.percentile(widths, 90))
    
    min_cell_size = 300
    target_height = max(target_height, min_cell_size)
    target_width = max(target_width, min_cell_size)
    
    # CRITICAL: Cap cell size to prevent massive memory usage
    max_cell_height = 1500
    max_cell_width = 1500
    if target_height > max_cell_height:
        Logger.debug(f"⚠️ Capping cell height from {target_height} to {max_cell_height}")
        target_height = max_cell_height
    if target_width > max_cell_width:
        Logger.debug(f"⚠️ Capping cell width from {target_width} to {max_cell_width}")
        target_width = max_cell_width
    
    fixed_font_scale, fixed_thickness, fixed_label_height = calculate_fixed_font_params(target_height, target_width)
    
    Logger.debug(f"Target cell size: {target_width}x{target_height}")
    Logger.debug(f"Fixed font parameters - Scale: {fixed_font_scale:.2f}, Thickness: {fixed_thickness}, Label height: {fixed_label_height}")
    
    # Add labels to images - process in batches
    labeled_imgs = []
    Logger.debug("Adding labels to crops...")
    
    for idx, (img_crop, label_num, scale_factor) in enumerate(valid_imgs):
        label = f"{label_num}"
        
        text_size = cv2.getTextSize(label, font, fixed_font_scale, fixed_thickness)[0]
        min_width_for_text = text_size[0] + 40
        final_crop_width = max(img_crop.shape[1], min_width_for_text)
        
        labeled_img = np.full((img_crop.shape[0] + fixed_label_height, final_crop_width, 3), 255, dtype=np.uint8)
        
        crop_x_offset = (final_crop_width - img_crop.shape[1]) // 2
        labeled_img[fixed_label_height:, crop_x_offset:crop_x_offset + img_crop.shape[1]] = img_crop
        
        text_x = (final_crop_width - text_size[0]) // 2
        text_y = fixed_label_height - max(int(fixed_label_height * 0.2), 8)
        
        cv2.putText(labeled_img, label, (text_x, text_y), font, fixed_font_scale, (0, 0, 255), fixed_thickness)
        labeled_imgs.append(labeled_img)
        
        # Clean up every 20 images
        if (idx + 1) % 20 == 0:
            gc.collect()
    
    # Clean up valid_imgs
    del valid_imgs
    gc.collect()
    Logger.debug("✓ Labels added")
    
    # Make all images fit in target size - process in batches
    Logger.debug("Padding images to uniform size...")
    padded_imgs = make_adaptive_size(labeled_imgs, (target_height, target_width))
    
    # Clean up labeled_imgs
    del labeled_imgs
    gc.collect()
    Logger.debug("✓ Images padded")
    
    # Create grid rows ONE AT A TIME to avoid huge memory spike
    Logger.debug("Creating grid rows...")
    num_rows = (len(padded_imgs) + regions_per_row - 1) // regions_per_row
    
    # Save rows to disk temporarily instead of keeping in memory
    temp_row_dir = os.path.join(output_dir, "temp_rows")
    os.makedirs(temp_row_dir, exist_ok=True)
    row_paths = []
    
    for row_idx in range(0, len(padded_imgs), regions_per_row):
        row_imgs = padded_imgs[row_idx:row_idx+regions_per_row]
        
        # Pad row if needed
        if len(row_imgs) < regions_per_row:
            blank = np.full((target_height, target_width, 3), 255, dtype=np.uint8)
            row_imgs += [blank] * (regions_per_row - len(row_imgs))
        
        # Concatenate this row
        row_img = np.concatenate(row_imgs, axis=1)
        
        # Save row to disk
        row_path = os.path.join(temp_row_dir, f"row_{row_idx // regions_per_row}.png")
        cv2.imwrite(row_path, row_img)
        row_paths.append(row_path)
        
        # Clean up
        del row_img, row_imgs
        gc.collect()
    
    Logger.debug(f"✓ Created {len(row_paths)} rows")
    
    # Clean up padded_imgs
    del padded_imgs
    gc.collect()
    
    # Now load rows one by one and combine them
    Logger.debug("Combining rows into final image...")
    rows = []
    for row_path in row_paths:
        row_img = cv2.imread(row_path)
        if row_img is not None:
            rows.append(row_img)
    
    # Combine rows
    stitched_img = np.concatenate(rows, axis=0)
    
    # Clean up rows
    del rows
    gc.collect()
    Logger.debug("✓ Rows combined")
    
    # Resize if too large
    max_dim = 7500
    h, w = stitched_img.shape[:2]
    if h > max_dim or w > max_dim:
        scale = min(max_dim / h, max_dim / w)
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(stitched_img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        del stitched_img
        stitched_img = resized
        Logger.debug(f"Resized final image from {w}x{h} to {new_w}x{new_h}")
    else:
        Logger.debug(f"Final image size: {w}x{h}")
    
    # Save with high quality
    original_filename = os.path.basename(original_image_path)
    output_path = os.path.join(output_dir, f"{os.path.splitext(original_filename)[0]}_dimensions{os.path.splitext(original_filename)[1]}")
    
    Logger.debug(f"Saving final image to {output_path}...")
    success = cv2.imwrite(output_path, stitched_img)
    
    # Clean up
    del stitched_img
    gc.collect()
    
    # Clean up temp row directory
    try:
        shutil.rmtree(temp_row_dir)
        Logger.debug("🧹 Cleaned up temporary row files")
    except:
        pass
    
    if success:
        Logger.debug(f"✓ Successfully saved dimension image to {output_path}")
        return output_path
    else:
        Logger.debug(f"❌ Failed to save {output_path}")
        return None

def CreateMetaDataImage(image_path, detections, output_dir="test", padding=10):   
    img = cv2.imread(image_path)
    if img is None:
        Logger.debug(f" Could not load image from {image_path}")
        return
    
    metadata_classes = ["Title_Block"]
    metadata_detections = [det for det in detections if det["class_name"] in metadata_classes]
    
    if not metadata_detections:
        Logger.debug("⚠️  No metadata detections found (Title Block, Revision Table, Miscellaneous)")
        return
    
    # Group detections by class and sort by x-coordinate
    class_groups = {}
    for det in metadata_detections:
        class_name = det["class_name"]
        if class_name not in class_groups:
            class_groups[class_name] = []
        class_groups[class_name].append(det)
    
    # Sort each class group by x-coordinate (left to right)
    for class_name in class_groups:
        class_groups[class_name].sort(key=lambda d: d["bbox"][0])  # Sort by x1
    
    # Extract crops row by row
    rows = []
    for class_name, class_dets in class_groups.items():
        row_crops = []
        
        for det in class_dets:
            x1, y1, x2, y2 = det["bbox"]
            
            # Add padding and ensure bounds
            x1 = max(0, int(x1) - padding)
            y1 = max(0, int(y1) - padding)
            x2 = min(img.shape[1], int(x2) + padding)
            y2 = min(img.shape[0], int(y2) + padding)
            
            # Extract crop
            crop = img[y1:y2, x1:x2]
            
            if crop.size > 0:
                row_crops.append(crop)
                Logger.debug(f"Added {class_name}: {crop.shape[1]}x{crop.shape[0]}")
        
        if row_crops:
            rows.append((class_name, row_crops))
    
    if not rows:
        Logger.debug(" No valid crops extracted")
        return
    
    # Calculate dimensions for each row
    row_heights = []
    row_widths = []
    
    for class_name, row_crops in rows:
        row_height = max(crop.shape[0] for crop in row_crops)
        row_width = sum(crop.shape[1] for crop in row_crops) + (len(row_crops) - 1) * 10  # 10px spacing between crops
        row_heights.append(row_height)
        row_widths.append(row_width)
    
    # Calculate canvas dimensions
    max_width = max(row_widths)
    total_height = sum(row_heights) + (len(rows) - 1) * 30 + 30  # 30px spacing between rows + label space
    
    # Create blank canvas
    stitched = np.ones((total_height, max_width, 3), dtype=np.uint8) * 255  # White background
    
    # Stitch rows
    current_y = 25  # Start with some top margin
    for (class_name, row_crops), row_height in zip(rows, row_heights):
        # Add class label
        cv2.putText(stitched, class_name, (10, current_y - 5), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
        
        # Calculate starting x for centering the row
        row_width = sum(crop.shape[1] for crop in row_crops) + (len(row_crops) - 1) * 10
        start_x = (max_width - row_width) // 2
        
        current_x = start_x
        for crop in row_crops:
            h, w = crop.shape[:2]
            
            # Center crop vertically within row height
            y_offset = current_y + (row_height - h) // 2
            
            # Place crop
            stitched[y_offset:y_offset + h, current_x:current_x + w] = crop
            
            current_x += w + 10  # Move to next position with horizontal spacing
        
        current_y += row_height + 30  # Move to next row with vertical spacing
    original_filename = os.path.basename(image_path)
    output_path = os.path.join(output_dir, f"{os.path.splitext(original_filename)[0]}_notes{os.path.splitext(original_filename)[1]}")
    # Save stitched image
    success = cv2.imwrite(output_path, stitched)
    if success:
        Logger.debug(f"Metadata image saved: {output_path} ({max_width}x{total_height})")
    else:
        Logger.debug(f" Failed to save {output_path}")
    
    return output_path


def FilterAnnotations(dimensions:dict):
    content_to_remove = [
        "NOTES:","NOTE:","NOTE","NOTES","UNLESS OTHER SPECIFIED",
        "UNLESS OTHERWISE SPECIFIED", "UNLESS OTHER SPECIFIED", 
        "GENERAL NOTES", "DETAIL", "IF IN DOUBT, ASK"
    ]

    DrawingAnnotations = dimensions["PageData"][0]["DrawingAnnotations"]
    # LLM uses DIM/GDT/NOTE; YOLO exports class names like Dimensions, GDnT, Notes — keep both.
    allowed_types = frozenset(
        {
            "DIM",
            "GDT",
            "NOTE",
            "Surface Finish",
            "Dimensions",
            "GDnT",
            "Notes",
            "Surface_Finish_Symbols",
            "Special_Characteristics",
        }
    )
    
    filtered_annotations = []
    for item in DrawingAnnotations:
        if item.get("AnnotationType") not in allowed_types:
            continue
            
        if item.get("AnnotationType") in ("NOTE", "Notes"):
            content_normalized = (item.get("content") or "").strip().upper()
            
            should_remove = False
            for pattern in content_to_remove:
                if content_normalized == pattern.upper():
                    should_remove = True
                    break
            
            if not should_remove:
                filtered_annotations.append(item)
        else:
            filtered_annotations.append(item)

    if filtered_annotations:
        start_id = min(item['id'] for item in filtered_annotations)
        for idx, item in enumerate(filtered_annotations, start=start_id):
            old_id = item['id']
            item['id'] = idx
            
    dimensions["PageData"][0]["DrawingAnnotations"] = filtered_annotations
    return dimensions


def getBalloonCoordinates(image_path, detections,
                            target_classes=YOLO_TARGET_CLASSES_FOR_CROPS):
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Could not load image from {image_path}")

    h, w = image.shape[:2]

    # Create occupancy map with larger grid
    occupancy_map = np.zeros((h // GRID_SIZE, w // GRID_SIZE), dtype=np.uint8)

    # Mark occupied areas based on existing detections
    for det in detections:
        x1, y1, x2, y2 = det["BBox"]
        y_start, y_end = max(0, y1 // GRID_SIZE), min(h // GRID_SIZE, y2 // GRID_SIZE + 1)
        x_start, x_end = max(0, x1 // GRID_SIZE), min(w // GRID_SIZE, x2 // GRID_SIZE + 1)
        occupancy_map[y_start:y_end, x_start:x_end] = 1

    balloon_coordinates = {}
    counter = 1

    for det in detections:
        cls_name = det["AnnotationType"]
        if cls_name not in target_classes:
            continue

        x1, y1, x2, y2 = det["BBox"]

        # ORIENTATION
        if (x2-x1) > (15*(y2-y1)):
            bbox_center = ((x1 + x2) // 2, y1, 0)  # horizontal
        else:
            bbox_center = (x1, (y1+ y2)//2, 1)     # vertical

        balloon_pos = findBalloonPosition(bbox_center, occupancy_map, w, h)

        balloon_number = det.get("id", counter)
        balloon_coordinates[balloon_number] = {'TextPos': balloon_pos}

        # Mark balloon area as occupied for future balloons
        map_x, map_y = balloon_pos[0] // GRID_SIZE, balloon_pos[1] // GRID_SIZE
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                ny, nx = map_y + dy, map_x + dx
                if 0 <= ny < occupancy_map.shape[0] and 0 <= nx < occupancy_map.shape[1]:
                    occupancy_map[ny, nx] = 1

        counter += 1

    return balloon_coordinates


def findBalloonPosition(bbox_center, occupancy_map, img_width, img_height):
    cx, cy, orientation = bbox_center
    best_pos = (cx, cy)

    if orientation == 1:        
        cx = int(cx - 0.007*img_width)
    else:        
        cy = int(cy - 0.005*img_height)
    best_pos = (cx, cy)

    min_crowding = float('inf')

    for radius in range(10, 40, 5):
        for angle in range(10, 360, 30):
            rad = math.radians(angle)
            x = int(cx + radius * math.cos(rad))
            y = int(cy + radius * math.sin(rad))

            if 25 < x < img_width - 25 and 25 < y < img_height - 25:
                map_x, map_y = x // GRID_SIZE, y // GRID_SIZE
                crowding = 0
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        ny, nx = map_y + dy, map_x + dx
                        if (0 <= ny < occupancy_map.shape[0] and
                            0 <= nx < occupancy_map.shape[1]):
                            crowding += occupancy_map[ny, nx]

                if crowding < min_crowding:
                    min_crowding = crowding
                    best_pos = (x, y)
                    if crowding == 0:
                        break

    return best_pos


def mergeAnnotations(annotations_list, dimensions_data):
    """
    Merge annotations list with dimensions data based on matching id and balloon_number
    """
    if not dimensions_data or not isinstance(dimensions_data, dict):
        dimensions_data = {"Dimensions": []}
    dims_list = dimensions_data.get("Dimensions")
    if not isinstance(dims_list, list):
        dims_list = []
    # Create a lookup dictionary from dimensions data
    dimensions_lookup = {}
    for dim in dims_list:
        try:
            balloon_num = int(dim["balloon_number"])
        except (TypeError, ValueError, KeyError):
            continue
        if "data" not in dim:
            continue
        dimensions_lookup[balloon_num] = dim["data"]
    
    # Merge the data
    merged_annotations = []
    for annotation in annotations_list:
        # Create a copy of the original annotation
        merged_annotation = annotation.copy()
        
        # If there's matching dimensions data, add it
        if annotation['id'] in dimensions_lookup:
            merged_annotation.update(dimensions_lookup[annotation['id']])
        
        merged_annotations.append(merged_annotation)
    
    return merged_annotations


def PdfToPreprocessedImage(pdf_path, output_dir, page_num=0, dpi=500, max_pixels=178000000):
    # Open PDF and get the specified page
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    
    # Calculate zoom factor from DPI
    zoom = dpi / 72.0
    
    # Get page dimensions and calculate resulting pixels
    page_rect = page.rect
    page_width, page_height = page_rect.width, page_rect.height
    estimated_pixels = int(page_width * zoom) * int(page_height * zoom)
    
    # If image exceeds maximum pixel limit, reduce DPI
    if estimated_pixels > max_pixels:
        safe_zoom = (max_pixels / (page_width * page_height)) ** 0.5
        zoom = min(zoom, safe_zoom)
    
    # Convert page to image
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img_data = pix.tobytes("png")
    # Convert to PIL Image
    pil_img = Image.open(io.BytesIO(img_data))
    # Convert PIL to numpy array for OpenCV processing
    img_array = np.array(pil_img)
    # Convert to grayscale
    gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    # Apply binary threshold to make image darker
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Invert so lines become white for dilation
    inv = cv2.bitwise_not(binary)
    # Dilate to thicken lines
    kernel = np.ones((3,3), np.uint8)
    thick = cv2.dilate(inv, kernel, iterations=2)
    # Invert back to get black lines on white background
    final_gray = cv2.bitwise_not(thick)
    # Convert back to PIL Image
    pil_img = Image.fromarray(final_gray)
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    # Close PDF
    doc.close()
    # Save image
    os.makedirs(output_dir, exist_ok=True)
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_filename = f"{pdf_name}_page_{page_num}.png"
    output_path = os.path.join(output_dir, output_filename)
    pil_img.save(output_path)
    return img, output_path


PREVIEW_IMAGE_MAX_SIDE = 2800


def run_drawing_yolo_detection(
    file_path: str,
    work_dir: str,
    original_filename: str,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Run YOLO on a saved PDF (first page, same preprocessing as Auto Ballooning) or raster.

    Returns (response_dict, error_message). Boxes are in the same pixel space as
    width/height. When preview_image_base64 is set, the client must draw on that image
    (PDF and oversized rasters).
    """
    ext = Path(original_filename or "").suffix.lower()
    infer_path = file_path

    try:
        if ext == ".pdf":
            _, infer_path = PdfToPreprocessedImage(file_path, work_dir, page_num=0)
            kind = "pdf"
            is_pdf = True
        else:
            kind = "raster"
            is_pdf = False

        img = cv2.imread(infer_path)
        if img is None:
            return None, "Could not read image for inference."

        h, w = img.shape[:2]
        long_side = max(w, h)
        infer_imgsz, infer_conf = yolo_autoballoon_inference_params(long_side, is_pdf)
        preds = RunInference(None, infer_path, imgsz=infer_imgsz, conf_thres=infer_conf)

        out = []
        for d in preds:
            bb = d.get("bbox")
            if not bb or len(bb) < 4:
                continue
            out.append(
                {
                    "class_name": d.get("class_name"),
                    "confidence": d.get("conf"),
                    "bbox": [int(bb[0]), int(bb[1]), int(bb[2]), int(bb[3])],
                }
            )

        preview_b64 = None
        disp_w, disp_h = w, h
        disp_dets = out

        need_inline = kind == "pdf" or long_side > PREVIEW_IMAGE_MAX_SIDE
        if need_inline and long_side > PREVIEW_IMAGE_MAX_SIDE:
            sc = PREVIEW_IMAGE_MAX_SIDE / long_side
            disp_w = int(w * sc)
            disp_h = int(h * sc)
            disp = cv2.resize(img, (disp_w, disp_h), interpolation=cv2.INTER_AREA)
            disp_dets = []
            for d in out:
                bb = d["bbox"]
                disp_dets.append(
                    {
                        "class_name": d["class_name"],
                        "confidence": d["confidence"],
                        "bbox": [
                            int(bb[0] * sc),
                            int(bb[1] * sc),
                            int(bb[2] * sc),
                            int(bb[3] * sc),
                        ],
                    }
                )
        elif kind == "pdf":
            disp = img.copy()
        else:
            disp = None

        if need_inline and disp is not None:
            ok, buf = cv2.imencode(".jpg", disp, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            if ok:
                preview_b64 = "data:image/jpeg;base64," + base64.standard_b64encode(buf.tobytes()).decode(
                    "ascii"
                )

        # Full-resolution bboxes (same pixel space as the on-disk infer image / PdfToPreprocessedImage).
        # `detections` may be scaled for preview when long_side > PREVIEW_IMAGE_MAX_SIDE — server-side
        # crops and OCR must use detections_full or bbox crops will be wrong (often blank/white).
        return (
            {
                "width": disp_w,
                "height": disp_h,
                "count": len(disp_dets),
                "detections": disp_dets,
                "detections_full": out,
                "infer_image_path": infer_path,
                "input_kind": kind,
                "preview_image_base64": preview_b64,
            },
            None,
        )
    except Exception as e:
        Logger.error("run_drawing_yolo_detection failed: %s", e)
        return None, str(e)


def ProcessAllGDTAnnotations(json_data, mapping=None):
 
    try:
        # Extract annotations from JSON
        annotations = json_data["PageData"][0]["DrawingAnnotations"]
        
        # Step 1: Add AssociatedDimension to all GDT annotations with default "NA"
        for ann in annotations:
            if ann.get("AnnotationType") == "GDT":
                ann["AssociatedDimension"] = "NA"
                
        
        return json_data
        
    except Exception as e:
        #print(f"Error processing GDT annotations: {e}")
        return json_data

def ProcessGDTAnnotations(json_data, mapping=None):
    """
    Complete function to add AssociatedDimension to all GDT annotations
    and update values based on mapping.
    
    Args:
        json_data: Dictionary containing annotation data
        mapping: Optional dictionary with id:AssociatedDimension pairs
        
    Returns:
        dict: Modified json_data
    """
    try:
        # Extract annotations from JSON
        annotations = json_data["PageData"][0]["DrawingAnnotations"]
        
        if mapping:
            updated_count = 0
            for ann in annotations:
                ann_id = ann.get("id")
                
                # Check if this annotation ID exists in the mapping
                if str(ann_id) in mapping or ann_id in mapping:
                    associated_dim = mapping.get(str(ann_id)) or mapping.get(ann_id)
                    
                    # Convert to integer if it's a numeric string, otherwise keep as is
                    if isinstance(associated_dim, str):
                        try:
                            associated_dim = int(associated_dim)
                        except ValueError:
                            # If conversion fails, keep as string (for "NA" or other non-numeric values)
                            pass
                    
                    ann["AssociatedDimension"] = associated_dim
                    updated_count += 1
                    #print(f"Updated annotation ID {ann_id}: AssociatedDimension = {associated_dim} (type: {type(associated_dim).__name__})")
            
            #print(f"Step 2: Updated {updated_count} annotations from mapping")
        
        return json_data
        
    except Exception as e:
        #print(f"Error processing GDT annotations: {e}")
        return json_data




def FilterAnnotationsWithMMCOrLMC(json_data):
    """
    Returns a list of annotation IDs that contain MMC (Ⓜ) or LMC (Ⓛ) symbols.
    
    Args:
        json_data: Dictionary containing annotation data
        
    Returns:
        list: List of annotation IDs (integers) with MMC/LMC symbols
    """
    try:
        # Extract annotations from JSON
        annotations = json_data["PageData"][0]["DrawingAnnotations"]
        
        # Symbols to look for
        mmc_symbol = "Ⓜ"  # Maximum Material Condition
        lmc_symbol = "Ⓛ"  # Least Material Condition
        
        filtered_ids = []
        
        for ann in annotations:
            ann_id = ann.get("id")
            ann_type = ann.get("AnnotationType")
            has_symbol = False
            
            # Check in Data field (for GDT annotations)
            if ann_type == "GDT":
                data = ann.get("Data", [])
                # Convert nested list to string for searching
                data_str = str(data)
                
                if mmc_symbol in data_str or lmc_symbol in data_str:
                    has_symbol = True
            
            # Check in representation field (for DIM annotations)
            representation = ann.get("representation", "")
            if mmc_symbol in representation or lmc_symbol in representation:
                has_symbol = True
            
            # Check in annotationName field
            annotation_name = ann.get("annotationName", "")
            if mmc_symbol in annotation_name or lmc_symbol in annotation_name:
                has_symbol = True
            
            if has_symbol and ann_id not in filtered_ids:
                filtered_ids.append(ann_id)
        
        return filtered_ids
        
    except Exception as e:
        #print(f"Error filtering annotations: {e}")
        return []

def ProcessImageAndJson(inputImage, json_data):
    """
    Takes an image and JSON data as input, draws balloon text with circle around it
    and saves the ballooned image in the same directory.
    """
    try:
        annotations = json_data["PageData"][0]["DrawingAnnotations"]

        img = Image.open(inputImage)

        if img.mode != 'RGB':
            img = img.convert('RGB')

        draw = ImageDraw.Draw(img)

        # Slightly bigger font size
        FONT_SIZE = 90

        # Try to load a TTF font
        try:
            font = ImageFont.truetype("arial.ttf", FONT_SIZE)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", FONT_SIZE)
            except:
                font = ImageFont.load_default()

        for ann in annotations:
            try:
                ann_id = ann.get("id")
                text_pos = ann.get("TextPos", [])

                if text_pos and len(text_pos) == 2:
                    x, y = int(text_pos[0]), int(text_pos[1])
                    text = str(ann_id)

                    # Measure text size
                    text_bbox = draw.textbbox((x, y), text, font=font)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_height = text_bbox[3] - text_bbox[1]

                    # Determine circle radius based on text size
                    radius = max(text_width, text_height) // 2 + 20  # Padding for the balloon

                    # Compute center
                    center_x = x + text_width // 2
                    center_y = y + text_height // 2

                    # Circle bounding box
                    left = center_x - radius
                    top = center_y - radius
                    right = center_x + radius
                    bottom = center_y + radius

                    # Draw circular balloon in red
                    draw.ellipse((left, top, right, bottom), outline=(255, 0, 0), width=6)

                    # Draw the ID text
                    draw.text((x, y), text, fill=(255, 0, 0), font=font)

            except Exception as ann_error:
                traceback.print_exc()
                continue

        # Output path
        input_dir = os.path.dirname(inputImage) or "."
        name, ext = os.path.splitext(os.path.basename(inputImage))
        output_path = os.path.join(input_dir, f"{name}_ballooned{ext}")

        #print(f"Saving ballooned image to: {output_path}")
        img.save(output_path)
        #print("Image saved successfully!")

        return output_path

    except Exception as e:
        #print(f"Unexpected error occurred: {e}")
        traceback.print_exc()
        return None


def AutoBallooning(pdf_path, weights, output_dir):
    try:
        name = os.path.splitext(os.path.basename(pdf_path))[0]
        input_ext = os.path.splitext(pdf_path)[1].lower()
        is_pdf_input = input_ext == ".pdf"

        reader = None
        if is_pdf_input:
            reader = PdfReader(pdf_path)
            # Get total number of pages
            total_pages = len(reader.pages)
            Logger.info(f"Total pages in PDF: {total_pages}")
            if total_pages == 0:
                raise Exception("PDF has no pages to process")
        else:
            # Non-PDF input (png/jpg/jpeg/etc.) is treated as a single-page drawing.
            probe_image = cv2.imread(pdf_path)
            if probe_image is None:
                raise Exception(f"Unsupported or unreadable input file: {pdf_path}")
            total_pages = 1
            Logger.info(f"Non-PDF input detected ({input_ext or 'unknown'}), processing as one page")

        # Initialize response structure
        response = {
            "Name": name,
            "PageData": []
        }
        
        # Track balloon numbers across pages
        next_balloon_number = 1
        page_failures = []

        # Process each page
        for page_num in range(total_pages):
            try:
                Logger.info(f"Processing page {page_num + 1} of {total_pages}")
                
                # Convert PDF page to image, or reuse non-PDF image directly.
                if is_pdf_input:
                    image, image_path = PdfToPreprocessedImage(pdf_path, output_dir, page_num=page_num)
                else:
                    image_path = pdf_path
                    image = cv2.imread(image_path)
                    if image is None:
                        raise Exception(f"Failed to read image input: {image_path}")
                viewsDir, coordinate_mappings = detectViews(image_path, output_dir)
            
                # Image dimensions in pixels
                image_height_px, image_width_px = image.shape[:2]
                
                if is_pdf_input:
                    # Get the current page
                    page = reader.pages[page_num]
                    # Extract width and height (in points - 1 point = 1/72 inch)
                    pdf_width_pts = float(page.mediabox.width)
                    pdf_height_pts = float(page.mediabox.height)
                else:
                    # For image inputs, keep 1:1 coordinate mapping in pixel space.
                    pdf_width_pts = float(image_width_px)
                    pdf_height_pts = float(image_height_px)

                # Calculate scale factors
                scale_factor_x = pdf_width_pts / image_width_px
                scale_factor_y = pdf_height_pts / image_height_px
                
                use_separate_scales = True  # Set False to use average scaling

                if use_separate_scales:
                    scale_x = scale_factor_x
                    scale_y = scale_factor_y
                else:
                    scale_factor = (scale_factor_x + scale_factor_y) / 2
                    scale_x = scale_y = scale_factor
                
                long_side = max(image_width_px, image_height_px)
                infer_imgsz, infer_conf = yolo_autoballoon_inference_params(long_side, is_pdf_input)
                Logger.info(
                    "YOLO inference params: long_side=%s imgsz=%s conf=%s (pdf=%s)",
                    long_side,
                    infer_imgsz,
                    infer_conf,
                    is_pdf_input,
                )
                dimensionsImage, detections = ProcessMultipleViews(
                    viewsDir,
                    coordinate_mappings,
                    weights,
                    image_path,
                    output_dir,
                    imgsz=infer_imgsz,
                    conf_thres=infer_conf,
                )
                preds = RunInference(
                    weights, image_path, imgsz=infer_imgsz, conf_thres=infer_conf
                )
                if not detections and preds:
                    Logger.info(
                        "Multi-view pipeline returned 0 annotations; using %d full-image YOLO boxes",
                        len(preds),
                    )
                    detections = yolo_raw_detections_to_annotation_list(preds)

                titleBlockImage = CreateMetaDataImage(image_path, preds, output_dir)
                text_pos_dict = getBalloonCoordinates(image_path, detections)
                
                if titleBlockImage:
                    titleBlockData = ExtractTitleBlockData(titleBlockImage)
                    dimensionPrompt = DIMENSION_EXTRACTION_PROMPT.format(titleBlockData=titleBlockData)
                else:
                    dimensionPrompt = DIMENSION_EXTRACTION_PROMPT.format(titleBlockData="No Title Block data available")
                
                if not dimensionsImage:
                    Logger.info("No dimension crops produced; skipping LLM dimension extraction for this page")
                    dimensions = {"Dimensions": []}
                else:
                    dimensions = ExtractDimensions(dimensionsImage, dimensionPrompt)
                    dimensions = ConvertToJson(dimensions)
                    if dimensions is None:
                        Logger.warning("LLM returned non-JSON dimensions; using empty Dimensions")
                        dimensions = {"Dimensions": []}
                
                DrawingAnnotations = mergeAnnotations(detections, dimensions)
                
                # For first page, keep original balloon numbers
                # For subsequent pages, renumber based on the last balloon from previous page
                if page_num > 0:
                    id_mapping = {}  # Map old IDs to new IDs
                    for annotation in DrawingAnnotations:
                        if 'id' in annotation:
                            old_id = annotation['id']
                            new_id = next_balloon_number
                            id_mapping[old_id] = new_id
                            annotation['id'] = new_id
                            next_balloon_number += 1
                    
                    # Update text_pos_dict with new IDs
                    updated_text_pos_dict = {}
                    for old_id, data in text_pos_dict.items():
                        if old_id in id_mapping:
                            updated_text_pos_dict[id_mapping[old_id]] = data
                        else:
                            updated_text_pos_dict[old_id] = data
                    text_pos_dict = updated_text_pos_dict
                
                # Scale bounding boxes from image coordinates to PDF coordinates
                for item in DrawingAnnotations:
                    if 'BBox' in item:
                        bbox = item['BBox']  # [x1, y1, x2, y2] in image coordinates
                        
                        if use_separate_scales:
                            bbox_pnts = [
                                bbox[0] * scale_x,                              # x1
                                pdf_height_pts - (bbox[1] * scale_y),           # y1 (flipped)
                                bbox[2] * scale_x,                              # x2
                                pdf_height_pts - (bbox[3] * scale_y)            # y2 (flipped)
                            ]
                        else:
                            bbox_pnts = [
                                bbox[0] * scale_factor,
                                pdf_height_pts - (bbox[1] * scale_factor),
                                bbox[2] * scale_factor,
                                pdf_height_pts - (bbox[3] * scale_factor)
                            ]
                        
                        # Ensure y1 < y2 after flipping
                        x1, y1, x2, y2 = bbox_pnts
                        bbox_pnts = [x1, min(y1, y2), x2, max(y1, y2)]
                        
                        item['BBoxPnts'] = bbox_pnts

                # Scale text positions from image coordinates to PDF coordinates
                for annotation in DrawingAnnotations:
                    annotation_id = annotation.get('id')
                    
                    if annotation_id in text_pos_dict:
                        text_pos = text_pos_dict[annotation_id]['TextPos']  # (x,y) in image coords
                        
                        annotation['TextPos'] = text_pos  # Keep original image-space coords
                        
                        if use_separate_scales:
                            x_scaled = text_pos[0] * scale_x
                            y_scaled = pdf_height_pts - (text_pos[1] * scale_y)  # flip y
                        else:
                            x_scaled = text_pos[0] * scale_factor
                            y_scaled = pdf_height_pts - (text_pos[1] * scale_factor)
                        
                        annotation['TextPosPnts'] = (x_scaled, y_scaled)

                # Create page data
                page_data = {
                    "pageNumber": page_num + 1,
                    "pdfWidth": f"{pdf_width_pts:.3f}",
                    "pdfHeight": f"{pdf_height_pts:.3f}",
                    "width": image_width_px,  # Image width in pixels
                    "height": image_height_px,  # Image height in pixels
                    "scaleFactorX": f"{scale_x:.6f}",
                    "scaleFactorY": f"{scale_y:.6f}",
                    "DrawingAnnotations": DrawingAnnotations
                }
                
                # Filter annotations for current page
                page_response = {
                    "Name": name,
                    "PageData": [page_data]
                }
                page_response = FilterAnnotations(page_response)
                
                # Process ballooned image
                balloonedImagePath = ProcessImageAndJson(image_path, page_response)

                page_response = ProcessAllGDTAnnotations(page_response)
                
                # Process GDT annotations if applicable
                filteredList = FilterAnnotationsWithMMCOrLMC(page_response)
                if len(filteredList) != 0:
                    if not balloonedImagePath:
                        raise Exception(
                            "ProcessImageAndJson did not produce a ballooned image (needed for GDT association). "
                            "Often caused by empty DrawingAnnotations or image/JSON mismatch."
                        )
                    filteredballoon_string = ','.join(map(str, filteredList))
                    associationPrompt = DIMENSION_ASSOCIATION_PROMPT.format(BalloonNumbers=filteredballoon_string)
                    res = AssociateDimensions(balloonedImagePath, associationPrompt)
                    dimensionAssociationJson = ConvertToJson(res)
                    page_response = ProcessGDTAnnotations(page_response, dimensionAssociationJson)
                
                # Append processed page data to main response
                response["PageData"].append(page_response["PageData"][0])
                
                # Update next_balloon_number based on the highest balloon ID in the current page's filtered annotations
                current_page_annotations = page_response["PageData"][0].get("DrawingAnnotations", [])
                if current_page_annotations:
                    max_balloon_id = max([ann.get('id', 0) for ann in current_page_annotations])
                    next_balloon_number = max_balloon_id + 1
                
                Logger.info(f"Completed page {page_num + 1}. Next balloon number will start from: {next_balloon_number}")
                
            except Exception as page_error:
                import traceback

                one_line = f"Page {page_num + 1}/{total_pages}: {page_error!s}"
                page_failures.append(one_line)
                Logger.error(f"Error processing page {page_num + 1}: {page_error}")
                Logger.error(traceback.format_exc())
                # Continue with next page instead of failing completely
                continue

        # Check if any pages were successfully processed
        if len(response["PageData"]) == 0:
            detail = (
                " | ".join(page_failures)
                if page_failures
                else "No exception details captured (failure before page loop or empty PDF)."
            )
            raise Exception(
                f"No pages were successfully processed. Per-page errors: {detail}"
            )

        return {"STATUS": "SUCCESS", "content": response}

    except Exception as e:
        Logger.error(f"Error in AutoBallooning: {e}")
        import traceback

        tb = traceback.format_exc()
        Logger.error(tb)
        return {
            "STATUS": "FAILURE",
            "content": {},
            "error": str(e),
            "traceback": tb[:8000],
        }
    
    finally:
        try:
            if os.path.exists(output_dir):
                shutil.rmtree(output_dir)
                Logger.info(f"Successfully deleted directory: {output_dir}")
        except OSError as e:
            Logger.error(f"Could not delete directory {output_dir}: {e}")


def run_upload_files(**kwargs):
    """Background Auto Ballooning job (no Celery — use from a thread in routes)."""
    partNumber = kwargs["partNumber"]
    revisionNumber = kwargs["revisionNumber"]
    fileCollection = db.GetCollection(kwargs["dataBaseName"], kwargs["collectionName"])
    try:
        Logger.debug("Adding files to knowledgebase started")
        fileCollection.update_one(
            {"part_number": partNumber, "revision_number": revisionNumber},
            {"$set": {"task_status": "STARTED"}},
        )
        Logger.debug(" Adding file to kb task started in background")
        Logger.debug("DB initialised")

        inputPath = kwargs["inputPath"]
        filePaths = kwargs["filePaths"]
        weights = resolve_autoballoon_weights_path()
        upload = AutoBallooning(inputPath, weights, filePaths)
        if not isinstance(upload, dict):
            upload = {"STATUS": "FAILURE", "content": {}, "error": f"Unexpected return type: {type(upload)!r}"}
        if upload.get("STATUS") == "SUCCESS":
            Logger.info("AutoBallooning completed successfully")
        else:
            Logger.error(f"AutoBallooning failed: {upload.get('error', upload)}")
        if upload["STATUS"] == "SUCCESS":
            jsonContent = upload["content"]
            Logger.info(f"Files uploaded successfully")
            fileCollection.update_one(
                {"part_number": partNumber, "revision_number": revisionNumber},
                {"$set": {"json_content": jsonContent, "task_status": "SUCCESS"}},
            )
        if upload["STATUS"] == "FAILURE":
            err = (upload.get("error") or "").strip() or "AutoBallooning returned FAILURE"
            Logger.error(f"Failed to upload files: {err}")
            fileCollection.update_one(
                {"part_number": partNumber, "revision_number": revisionNumber},
                {
                    "$set": {
                        "task_status": "FAILURE",
                        "task_error": err,
                        "task_traceback": (upload.get("traceback") or "")[:12000],
                    }
                },
            )
    except Exception as e:
        Logger.error(f"Upload files process failed: {e}")
        try:
            fileCollection.update_one(
                {"part_number": partNumber, "revision_number": revisionNumber},
                {"$set": {"task_status": "FAILURE", "task_error": str(e)}},
            )
        except Exception:
            pass


# Celery (optional production worker): not used by run_local / Auto Ballooning API path.
# @CeleryApp.task(bind=True, soft_time_limit=300)
# def UploadFiles(self, **kwargs):
#     run_upload_files(**kwargs)