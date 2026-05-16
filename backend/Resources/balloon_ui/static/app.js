(function () {
  const ORIGIN = window.location.origin;
  const cred = { credentials: "same-origin" };
  const apiBaseEl = document.getElementById("apiBase");
  if (apiBaseEl) apiBaseEl.textContent = ORIGIN;

  // ── Token bootstrap ──────────────────────────────────────────────────────
  // If the Dashboard passed ?token=<JWT> in the URL, store it in localStorage
  // (origin-scoped to 10000) then strip it from the URL so it isn't visible
  // in the address bar or browser history.
  (function bootstrapToken() {
    try {
      var params = new URLSearchParams(window.location.search);
      var urlToken = params.get("token");
      if (urlToken) {
        localStorage.setItem("balloon_token", urlToken);
        params.delete("token");
        var clean = window.location.pathname +
          (params.toString() ? "?" + params.toString() : "") +
          window.location.hash;
        history.replaceState(null, "", clean);
      }
    } catch (e) {
      console.warn("[auth] Token bootstrap failed:", e);
    }
  })();

  /** Returns the JWT stored for this origin, or null if not logged in. */
  function getAuthToken() {
    return localStorage.getItem("balloon_token") || null;
  }

  const fileInput = document.getElementById("file");
  const runBtn = document.getElementById("runBtn");
  const statusEl = document.getElementById("status");
  const panelInput = document.getElementById("panelInput");
  const panelBalloon = document.getElementById("panelBalloon");
  const jsonOut = document.getElementById("jsonOut");
  const resultBody = document.getElementById("resultBody");
  const downloadInput = document.getElementById("downloadInput");
  const downloadBalloon = document.getElementById("downloadBalloon");
  const downloadExcel = document.getElementById("downloadExcel");
  const inspectionReport = document.getElementById("inspectionReport");
  const INSPECTION_STORAGE_KEY = "smorx_inspection_payload";
  const adminLink = document.getElementById("adminLink");
  const dashboardBtn = document.getElementById("dashboardBtn");

  let lastFile = null;
  let lastJson = null;
  let lastBalloonCanvas = null;
  let balloonImageCache = null;
  /** Blob URL for PDF iframe preview — revoked when replaced or reset */
  let inputPdfPreviewUrl = null;
  /** @type {Record<number, { cx: number, cy: number }>} canvas-space coords for dragged balloons */
  let balloonUiOverrides = {};
  /** @type {null | 'create' | 'edit' | 'delete'} */
  let balloonMode = null;
  /** Drag state must live outside per-canvas closures so document-level move/up work after repaint. */
  let activeBalloonDrag = null;
  /** @type {null | { x1: number, y1: number, x2: number, y2: number }} canvas px */
  let pendingRectOverlay = null;

  const btnModeCreate = document.getElementById("btnModeCreate");
  const btnModeEdit = document.getElementById("btnModeEdit");
  const btnModeDelete = document.getElementById("btnModeDelete");
  const btnModeSave = document.getElementById("btnModeSave");
  const modeHintEl = document.getElementById("modeHint");
  const btnBalloonMenu = document.getElementById("btnBalloonMenu");
  const balloonQuickPanel = document.getElementById("balloonQuickPanel");
  const quickBalloonBody = document.getElementById("quickBalloonBody");
  const btnCloseQuickPanel = document.getElementById("btnCloseQuickPanel");
  const btnQuickSave = document.getElementById("btnQuickSave");
  const quickCropViewModal = document.getElementById("quickCropViewModal");
  const quickCropViewImg = document.getElementById("quickCropViewImg");
  const btnCloseCropModal = document.getElementById("btnCloseCropModal");
  const quickCropModalBackdrop = document.getElementById("quickCropModalBackdrop");
  /** Quick panel: moved to body + drag */
  let quickPanelOnBody = false;
  let quickPanelSavedPos = null;
  let quickPanelDrag = null;

  function getCanvasScale(det) {
    const iw = Number(det && det.width) || 1;
    const maxW = Math.min(1100, iw);
    return maxW / iw;
  }

  function canvasRectToDetectionBBox(cx1, cy1, cx2, cy2, det) {
    const sx = getCanvasScale(det);
    let x1 = Math.round(Math.min(cx1, cx2) / sx);
    let y1 = Math.round(Math.min(cy1, cy2) / sx);
    let x2 = Math.round(Math.max(cx1, cx2) / sx);
    let y2 = Math.round(Math.max(cy1, cy2) / sx);
    const iw = Number(det.width) || 1;
    const ih = Number(det.height) || 1;
    x1 = Math.max(0, Math.min(iw - 1, x1));
    y1 = Math.max(0, Math.min(ih - 1, y1));
    x2 = Math.max(x1 + 1, Math.min(iw, x2));
    y2 = Math.max(y1 + 1, Math.min(ih, y2));
    return [x1, y1, x2, y2];
  }

  function cropCanvasRegion(canvas, cx1, cy1, cx2, cy2) {
    const w = Math.max(1, Math.round(Math.abs(cx2 - cx1)));
    const h = Math.max(1, Math.round(Math.abs(cy2 - cy1)));
    const lx = Math.min(cx1, cx2);
    const ly = Math.min(cy1, cy2);
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    try {
      c.getContext("2d").drawImage(canvas, lx, ly, w, h, 0, 0, w, h);
    } catch (err) {
      return { preview: "", save: "" };
    }
    const u = c.toDataURL("image/jpeg", 0.92);
    return { preview: u, save: u };
  }

  function setBalloonModeButtons() {
    if (btnModeCreate) btnModeCreate.classList.toggle("mode-active", balloonMode === "create");
    if (btnModeEdit) btnModeEdit.classList.toggle("mode-active", balloonMode === "edit");
    if (btnModeDelete) btnModeDelete.classList.toggle("mode-active", balloonMode === "delete");
  }

  function setModeHint(text) {
    if (modeHintEl) modeHintEl.textContent = text || "";
  }

  function setBalloonToolsEnabled(on) {
    if (btnModeCreate) btnModeCreate.disabled = !on;
    if (btnModeEdit) btnModeEdit.disabled = !on;
    if (btnModeDelete) btnModeDelete.disabled = !on;
    if (btnModeSave) btnModeSave.disabled = !on;
    if (btnBalloonMenu) btnBalloonMenu.disabled = !on;
  }

  function ensureQuickPanelOnBody() {
    if (quickPanelOnBody || !balloonQuickPanel) return;
    document.body.appendChild(balloonQuickPanel);
    quickPanelOnBody = true;
    const head = balloonQuickPanel.querySelector(".balloon-quick-panel-head");
    if (head && !head.dataset.dragInit) {
      head.dataset.dragInit = "1";
      head.addEventListener("mousedown", onQuickPanelHeadDragStart);
    }
  }

  function placeQuickPanelForOpen() {
    if (!balloonQuickPanel || !btnBalloonMenu) return;
    balloonQuickPanel.style.position = "fixed";
    balloonQuickPanel.style.right = "auto";
    balloonQuickPanel.style.bottom = "auto";
    if (quickPanelSavedPos) {
      balloonQuickPanel.style.left = quickPanelSavedPos.left + "px";
      balloonQuickPanel.style.top = quickPanelSavedPos.top + "px";
      return;
    }
    const r = btnBalloonMenu.getBoundingClientRect();
    const estW = Math.min(window.innerWidth * 0.96, 832);
    const estH = 320;
    let left = r.right - estW;
    left = Math.max(8, Math.min(left, window.innerWidth - estW - 8));
    let top = r.bottom + 8;
    if (top + estH > window.innerHeight - 8) {
      top = Math.max(8, r.top - estH - 8);
    }
    balloonQuickPanel.style.left = left + "px";
    balloonQuickPanel.style.top = top + "px";
  }

  function onQuickPanelHeadDragStart(e) {
    if (!balloonQuickPanel || balloonQuickPanel.hidden) return;
    if (e.target.closest && e.target.closest(".btn-close-quick")) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = balloonQuickPanel.getBoundingClientRect();
    quickPanelDrag = {
      startX: e.clientX,
      startY: e.clientY,
      origLeft: rect.left,
      origTop: rect.top,
    };
    document.body.style.userSelect = "none";
  }

  function onQuickPanelDragMove(e) {
    if (!quickPanelDrag || !balloonQuickPanel) return;
    e.preventDefault();
    const dx = e.clientX - quickPanelDrag.startX;
    const dy = e.clientY - quickPanelDrag.startY;
    let nl = quickPanelDrag.origLeft + dx;
    let nt = quickPanelDrag.origTop + dy;
    const w = balloonQuickPanel.offsetWidth;
    const h = balloonQuickPanel.offsetHeight;
    nl = Math.max(0, Math.min(nl, window.innerWidth - w));
    nt = Math.max(0, Math.min(nt, window.innerHeight - h));
    balloonQuickPanel.style.left = nl + "px";
    balloonQuickPanel.style.top = nt + "px";
    balloonQuickPanel.style.right = "auto";
  }

  function onQuickPanelDragEnd() {
    document.body.style.userSelect = "";
    if (!quickPanelDrag || !balloonQuickPanel) {
      quickPanelDrag = null;
      return;
    }
    quickPanelDrag = null;
    const r = balloonQuickPanel.getBoundingClientRect();
    quickPanelSavedPos = { left: r.left, top: r.top };
  }

  document.addEventListener("mousemove", onQuickPanelDragMove);
  document.addEventListener("mouseup", onQuickPanelDragEnd);

  function openQuickCropModal(dataUrl) {
    if (!quickCropViewModal || !quickCropViewImg) return;
    if (!dataUrl) return;
    quickCropViewImg.src = dataUrl;
    quickCropViewModal.hidden = false;
  }

  function closeQuickCropModal() {
    if (!quickCropViewModal || !quickCropViewImg) return;
    quickCropViewModal.hidden = true;
    quickCropViewImg.removeAttribute("src");
  }

  function setQuickPanelOpen(open) {
    if (!balloonQuickPanel || !btnBalloonMenu) return;
    if (open) {
      ensureQuickPanelOnBody();
      buildQuickTableFromDetection();
      placeQuickPanelForOpen();
      balloonQuickPanel.hidden = false;
      btnBalloonMenu.setAttribute("aria-expanded", "true");
    } else {
      balloonQuickPanel.hidden = true;
      btnBalloonMenu.setAttribute("aria-expanded", "false");
    }
  }

  function appendQuickRowFromItem(it) {
    if (!quickBalloonBody) return;
    const tr = document.createElement("tr");
    function mk(cls, val, ph) {
      const inp = document.createElement("input");
      inp.type = "text";
      inp.className = "table-text-input " + cls;
      inp.value = val != null ? String(val) : "";
      inp.placeholder = ph || "";
      return inp;
    }
    const td1 = document.createElement("td");
    td1.appendChild(mk("qb-num", it.balloon_number, "#"));
    const td2 = document.createElement("td");
    td2.appendChild(mk("qb-class", it.class_name, "Classes"));
    const td3 = document.createElement("td");
    td3.appendChild(mk("qb-nom", it.nominal_value, "Nominal"));
    const td4 = document.createElement("td");
    td4.appendChild(mk("qb-tol", it.tolerance, "Tol"));
    const td5 = document.createElement("td");
    td5.appendChild(mk("qb-oth", it.others, "others"));
    const pv = it.crop_preview_base64 || "";
    const sv = it.crop_save_base64 || it.crop_preview_base64 || "";
    tr._qbCropPreview = pv;
    tr._qbCropSave = sv;
    const fullViewUrl = sv || pv;
    const tdCrop = document.createElement("td");
    tdCrop.className = "qb-col-crop";
    const cropWrap = document.createElement("div");
    cropWrap.className = "qb-crop-cell";
    if (pv || sv) {
      const thumbSrc = pv || sv;
      const im = document.createElement("img");
      im.className = "qb-crop-thumb";
      im.src = thumbSrc;
      im.alt = "Crop";
      im.title = "Click to view full size";
      im.style.cursor = fullViewUrl ? "pointer" : "default";
      im.addEventListener("mousedown", function (e) {
        e.stopPropagation();
      });
      im.addEventListener("click", function (e) {
        e.stopPropagation();
        e.preventDefault();
        if (fullViewUrl) openQuickCropModal(fullViewUrl);
      });
      cropWrap.appendChild(im);
    } else {
      const none = document.createElement("span");
      none.className = "qb-crop-none";
      none.textContent = "—";
      cropWrap.appendChild(none);
    }
    const btnView = document.createElement("button");
    btnView.type = "button";
    btnView.className = "btn-secondary btn-mini";
    btnView.textContent = "View";
    btnView.disabled = !fullViewUrl;
    btnView.addEventListener("mousedown", function (e) {
      e.stopPropagation();
    });
    btnView.addEventListener("click", function (e) {
      e.stopPropagation();
      e.preventDefault();
      if (fullViewUrl) openQuickCropModal(fullViewUrl);
    });
    cropWrap.appendChild(btnView);
    tdCrop.appendChild(cropWrap);
    const tdAct = document.createElement("td");
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn-secondary btn-mini";
    del.textContent = "Delete";
    del.addEventListener("mousedown", function (e) {
      e.stopPropagation();
    });
    del.addEventListener("click", function (e) {
      e.stopPropagation();
      e.preventDefault();
      tr.remove();
    });
    tdAct.appendChild(del);
    tr.appendChild(td1);
    tr.appendChild(td2);
    tr.appendChild(td3);
    tr.appendChild(td4);
    tr.appendChild(td5);
    tr.appendChild(tdCrop);
    tr.appendChild(tdAct);
    quickBalloonBody.appendChild(tr);
  }

  function buildQuickTableFromDetection() {
    if (!quickBalloonBody) return;
    quickBalloonBody.innerHTML = "";
    const items = (lastJson && lastJson.detection && lastJson.detection.balloon_items) || [];
    items.forEach(function (it) {
      appendQuickRowFromItem(it);
    });
  }

  function applyQuickTableToDetection() {
    if (!lastJson || !lastJson.detection || !quickBalloonBody) return;
    const det = lastJson.detection;
    const rows = quickBalloonBody.querySelectorAll("tr");
    const oldItems = det.balloon_items || [];
    const oldDets = det.detections || [];
    const oldAnns = det.drawing_annotations || [];
    const w = Number(det.width) || 1000;
    const h = Number(det.height) || 1000;
    const cx = Math.floor(w / 2);
    const cy = Math.floor(h / 2);
    const half = 48;
    const stubBbox = [
      Math.max(0, cx - half),
      Math.max(0, cy - half),
      Math.min(w, cx + half),
      Math.min(h, cy + half),
    ];

    if (rows.length === 0) {
      det.balloon_items = [];
      det.detections = [];
      det.drawing_annotations = [];
      det.count = 0;
      balloonUiOverrides = {};
      det.balloon_ui_overrides = {};
      renderResultTable(lastJson);
      paintBalloonCanvas();
      syncJsonFromTable();
      setStatus("Quick table saved — list cleared.");
      setQuickPanelOpen(false);
      return;
    }

    const newItems = [];
    const newDets = [];
    const newAnns = [];

    rows.forEach(function (tr, i) {
      const nRaw = tr.querySelector(".qb-num").value.trim();
      const numParsed = parseInt(nRaw, 10);
      const balloonNum = Number.isFinite(numParsed) && numParsed > 0 ? numParsed : i + 1;
      const cls = tr.querySelector(".qb-class").value.trim() || "Manual";
      const nom = tr.querySelector(".qb-nom").value;
      const tol = tr.querySelector(".qb-tol").value;
      const oth = tr.querySelector(".qb-oth").value;

      const base = Object.assign({}, oldItems[i] || {});
      const merged = Object.assign(base, {
        balloon_number: balloonNum,
        class_name: cls,
        confidence: base.confidence != null ? base.confidence : 1,
        nominal_value: nom,
        tolerance: tol,
        others: oth,
      });
      if (tr._qbCropPreview !== undefined) merged.crop_preview_base64 = tr._qbCropPreview;
      if (tr._qbCropSave !== undefined) merged.crop_save_base64 = tr._qbCropSave;
      newItems.push(merged);

      if (i < oldDets.length) {
        const d = Object.assign({}, oldDets[i]);
        d.class_name = cls;
        newDets.push(d);
        const ann = Object.assign({}, oldAnns[i]);
        ann.id = balloonNum;
        ann.AnnotationType = cls;
        newAnns.push(ann);
      } else {
        newDets.push({
          class_name: cls,
          confidence: 1,
          bbox: stubBbox.slice(),
          _manual: true,
          _fromQuickTable: true,
        });
        newAnns.push({
          id: balloonNum,
          AnnotationType: cls,
          BBox: stubBbox.slice(),
          TextPos: [(stubBbox[0] + stubBbox[2]) / 2, (stubBbox[1] + stubBbox[3]) / 2],
        });
      }
    });

    det.balloon_items = newItems;
    det.detections = newDets;
    det.drawing_annotations = newAnns;
    det.count = newDets.length;

    const nextOv = {};
    for (let i = 0; i < newAnns.length; i++) {
      const bid = newAnns[i].id;
      if (balloonUiOverrides[bid]) {
        nextOv[bid] = balloonUiOverrides[bid];
      }
    }
    balloonUiOverrides = nextOv;
    det.balloon_ui_overrides = Object.assign({}, nextOv);

    renderResultTable(lastJson);
    paintBalloonCanvas();
    syncJsonFromTable();
    setStatus("Quick table saved — same values appear in Detected details below.");
    setQuickPanelOpen(false);
  }

  function applyBalloonSave() {
    if (!lastJson || !lastJson.detection) return;
    const det = lastJson.detection;
    const sx = getCanvasScale(det);
    const anns = det.drawing_annotations || [];
    for (let i = 0; i < anns.length; i++) {
      const ann = anns[i];
      const o = balloonUiOverrides[ann.id];
      if (o && Number.isFinite(o.cx) && Number.isFinite(o.cy)) {
        ann.TextPos = [o.cx / sx, o.cy / sx];
      }
    }
    det.balloon_ui_overrides = Object.assign({}, balloonUiOverrides);
    det.count = (det.detections || []).length;
    syncJsonFromTable();
    renderResultTable(lastJson);
    paintBalloonCanvas();
    setStatus("Saved — JSON, table, and canvas updated.");
    setModeHint("Saved. Continue with Create / Edit, or Save again after more changes.");
  }

  function bboxesRoughlyEqual(a, b) {
    if (!a || !b || a.length < 4 || b.length < 4) return false;
    for (let i = 0; i < 4; i++) {
      if (Math.abs(Number(a[i]) - Number(b[i])) > 1.5) return false;
    }
    return true;
  }

  /**
   * Same vision LLM extract as automatic detection — fills nominal / tolerance / others for a manual box.
   */
  function fetchExtractForManualBalloon(bboxRef) {
    const det = lastJson && lastJson.detection;
    if (!det) return;
    const dets = det.detections || [];
    const items = det.balloon_items || [];
    let idx = -1;
    for (let i = 0; i < dets.length; i++) {
      if (!dets[i]._manual) continue;
      if (bboxesRoughlyEqual(dets[i].bbox, bboxRef)) {
        idx = i;
        break;
      }
    }
    if (idx < 0 || idx >= items.length) return;
    const it = items[idx];
    const imgData = it.crop_save_base64 || it.crop_preview_base64;
    if (!imgData) return;
    setStatus("Extracting text from manual crop…");
    fetch(ORIGIN + "/api/v1/extract-balloon-text", {
      method: "POST",
      headers: (function () {
        var h = { "Content-Type": "application/json" };
        var t = getAuthToken(); if (t) h["Authorization"] = "Bearer " + t;
        return h;
      })(),
      body: JSON.stringify({ crop_jpeg_base64: imgData, class_name: "Manual" }),
      credentials: "same-origin",
    })
      .then(function (r) {
        if (authRedirect(r.status)) return null;
        return r.json();
      })
      .then(function (j) {
        if (!j || !j.ok || !j.extract) {
          setStatus("Manual balloon added (text extract unavailable). Save to keep the box.");
          return;
        }
        const ex = j.extract;
        it.nominal_value = ex.nominal_value != null ? String(ex.nominal_value) : "";
        it.tolerance = ex.tolerance != null ? String(ex.tolerance) : "";
        it.others = ex.others != null ? String(ex.others) : "";
        syncJsonFromTable();
        renderResultTable(lastJson);
        setStatus("Manual balloon: nominal / tolerance filled like auto-detect.");
      })
      .catch(function () {
        setStatus("Manual balloon added (extract request failed). Save to keep the box.");
      });
  }

  function addManualDetectionFromRect(canvas, cx1, cy1, cx2, cy2) {
    if (!lastJson || !lastJson.detection) return;
    const det = lastJson.detection;
    const bbox = canvasRectToDetectionBBox(cx1, cy1, cx2, cy2, det);
    if (bbox[2] - bbox[0] < 4 || bbox[3] - bbox[1] < 4) return;
    const crops = cropCanvasRegion(canvas, cx1, cy1, cx2, cy2);
    const dets = det.detections || [];
    const annId = dets.length + 1;
    dets.push({
      class_name: "Manual",
      confidence: 1,
      bbox: bbox,
      _manual: true,
    });
    det.detections = dets;
    const anns = det.drawing_annotations || [];
    anns.push({
      id: annId,
      AnnotationType: "Manual",
      BBox: bbox.slice(),
      TextPos: [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
    });
    det.drawing_annotations = anns;
    const items = det.balloon_items || [];
    items.push({
      balloon_number: annId,
      class_name: "Manual",
      confidence: 1,
      nominal_value: "",
      tolerance: "",
      others: "",
      bbox_pixels: bbox.slice(),
      crop_preview_base64: crops.preview,
      crop_save_base64: crops.save,
      manual: true,
    });
    det.balloon_items = items;
    det.count = dets.length;
    const bboxForExtract = bbox.slice();
    renumberBalloonsReadingOrder();
    syncJsonFromTable();
    renderResultTable(lastJson);
    paintBalloonCanvas();
    setStatus("Balloon added. Numbers follow top→bottom, left→right. Use Save to commit.");
    fetchExtractForManualBalloon(bboxForExtract);
  }

  /**
   * Sort by bbox center: top→bottom, then left→right. Assign ids 1…n (same as backend tblr rules).
   */
  function renumberBalloonsReadingOrder() {
    if (!lastJson || !lastJson.detection) return;
    const det = lastJson.detection;
    const dets = det.detections || [];
    const anns = det.drawing_annotations || [];
    const items = det.balloon_items || [];
    const n = Math.min(dets.length, anns.length, items.length);
    if (n === 0) {
      if (dets.length === 0 && anns.length === 0) {
        det.balloon_items = [];
        det.count = 0;
        balloonUiOverrides = {};
        det.balloon_ui_overrides = {};
      }
      return;
    }

    /** Top edge (y1), then left edge (x1) — stable left-to-right on the same row. */
    function bboxReadingOrderKey(di) {
      let bb = dets[di] && dets[di].bbox;
      if (!bb || bb.length < 4) {
        const a = anns[di];
        bb = a && a.BBox;
      }
      if (!bb || bb.length < 4) return [1e30, 1e30];
      return [Number(bb[1]), Number(bb[0])];
    }

    const indices = [];
    for (let i = 0; i < n; i++) indices.push(i);
    indices.sort(function (ia, ib) {
      const ka = bboxReadingOrderKey(ia);
      const kb = bboxReadingOrderKey(ib);
      if (ka[0] !== kb[0]) return ka[0] - kb[0];
      if (ka[1] !== kb[1]) return ka[1] - kb[1];
      return ia - ib;
    });

    const mergedOv = Object.assign({}, det.balloon_ui_overrides || {}, balloonUiOverrides);
    const newDets = [];
    const newAnns = [];
    const newItems = [];
    const newOverrides = {};
    for (let j = 0; j < n; j++) {
      const oi = indices[j];
      const newId = j + 1;
      const oldId = anns[oi].id;
      newDets.push(dets[oi]);
      const ann = Object.assign({}, anns[oi]);
      ann.id = newId;
      newAnns.push(ann);
      const it = Object.assign({}, items[oi]);
      it.balloon_number = newId;
      newItems.push(it);
      if (mergedOv[oldId] && Number.isFinite(mergedOv[oldId].cx) && Number.isFinite(mergedOv[oldId].cy)) {
        newOverrides[newId] = mergedOv[oldId];
      }
    }

    det.detections = newDets;
    det.drawing_annotations = newAnns;
    det.balloon_items = newItems;
    det.count = newDets.length;
    balloonUiOverrides = newOverrides;
    det.balloon_ui_overrides = Object.assign({}, newOverrides);
  }

  function deleteBalloonById(balloonId) {
    if (!lastJson || !lastJson.detection) return false;
    const det = lastJson.detection;
    const anns = det.drawing_annotations || [];
    const idx = anns.findIndex(function (a) {
      return Number(a.id) === Number(balloonId);
    });
    if (idx < 0) return false;

    const dets = det.detections || [];
    const items = det.balloon_items || [];

    if (idx < dets.length) dets.splice(idx, 1);
    anns.splice(idx, 1);
    if (idx < items.length) items.splice(idx, 1);

    det.detections = dets;
    det.drawing_annotations = anns;
    det.balloon_items = items;

    renumberBalloonsReadingOrder();
    syncJsonFromTable();
    renderResultTable(lastJson);
    paintBalloonCanvas();
    setStatus("Removed balloon. Numbers follow top→bottom, left→right. Click Save to commit.");
    return true;
  }

  function setInputDownloadEnabled(on) {
    if (downloadInput) downloadInput.disabled = !on;
  }

  function setBalloonDownloadEnabled(on) {
    if (downloadBalloon) downloadBalloon.disabled = !on;
  }

  function setExcelDownloadEnabled(on) {
    if (downloadExcel) downloadExcel.disabled = !on;
  }

  function setInspectionReportEnabled(on) {
    if (!inspectionReport) return;
    inspectionReport.disabled = !on;
    inspectionReport.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function setStatus(t) {
    if (statusEl) statusEl.textContent = t || "";
  }

  // ── Toast notifications ──────────────────────────────────────────────────
  function showToast(msg, type) {
    // type: 'error' | 'success' | 'info'
    var existing = document.getElementById("_dashToast");
    if (existing) existing.remove();
    var toast = document.createElement("div");
    toast.id = "_dashToast";
    var bg = type === "error" ? "#c0392b" : type === "success" ? "#1a6640" : "#1a3a5c";
    toast.style.cssText = [
      "position:fixed", "top:1.25rem", "left:50%", "transform:translateX(-50%)",
      "background:" + bg, "color:#fff", "padding:0.75rem 1.4rem",
      "border-radius:8px", "font-size:0.9rem", "font-weight:600",
      "box-shadow:0 4px 18px rgba(0,0,0,0.45)", "z-index:99999",
      "max-width:90vw", "text-align:center", "pointer-events:none",
    ].join(";");
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(function () { if (toast.parentNode) toast.remove(); }, 4000);
  }

  // ── Full session reset (reused by file-change and dashboard redirect) ────
  function resetSession() {
    lastFile = null;
    lastJson = null;
    lastBalloonCanvas = null;
    balloonImageCache = null;
    balloonUiOverrides = {};
    balloonMode = null;
    pendingRectOverlay = null;
    detachBalloonDragListeners();
    activeBalloonDrag = null;
    setBalloonModeButtons();
    setBalloonToolsEnabled(false);
    setModeHint("Run auto ballooning first. Then use Create (draw box), Edit (drag balloons), Save (apply everywhere).");
    if (jsonOut) jsonOut.textContent = "{}";
    setBalloonDownloadEnabled(false);
    setExcelDownloadEnabled(false);
    setInspectionReportEnabled(false);
    if (resultBody) resultBody.innerHTML = "<tr><td colspan=\"5\">Run auto ballooning to see extracted values.</td></tr>";
    clearPanel(panelBalloon, "Run auto ballooning to see balloons here.");
    clearPanel(panelInput);
    setInputDownloadEnabled(false);
    if (fileInput) { try { fileInput.value = ""; } catch (_) {} }
    revokeInputPdfPreview();
    quickPanelSavedPos = null;
    setQuickPanelOpen(false);
    setStatus("");
  }

  function authRedirect(status) {
    if (status === 401) {
      window.location.href = "/login";
      return true;
    }
    if (status === 402) {
      window.location.href = "/payment";
      return true;
    }
    return false;
  }

  if (dashboardBtn) {
    dashboardBtn.addEventListener("click", async function () {
      const token = getAuthToken();

      if (!token) {
        showToast("You are not logged in. Please log in first.", "error");
        return;
      }

      // Capture the annotated canvas as a JPEG base64 thumbnail
      let previewB64 = null;
      if (lastBalloonCanvas) {
        try {
          previewB64 = lastBalloonCanvas.toDataURL("image/jpeg", 0.7);
        } catch (e) {
          console.warn("[dashboard] Could not capture canvas preview:", e);
        }
      }

      // Detect which JSON layout the pipeline returned
      // Older layout: lastJson.detection.balloon_items
      // Newer layout: lastJson.balloon_items
      const det = (lastJson && lastJson.detection) ? lastJson.detection : lastJson;

      function stripCrop(item) {
        const c = Object.assign({}, item);
        delete c.crop_save_base64;
        delete c.crop_preview_base64;  // large base64 thumbnail — not needed in DB
        return c;
      }

      // table_data — flat list used for the balloon items table
      let tableData = null;
      if (det && det.balloon_items) {
        tableData = det.balloon_items.map(stripCrop);
      }

      // balloon_data — full detection payload (crops stripped)
      let balloonData = null;
      if (det) {
        balloonData = Object.assign({}, det);
        if (balloonData.balloon_items) {
          balloonData.balloon_items = balloonData.balloon_items.map(stripCrop);
        }
      }

      const filename = (lastFile && lastFile.name) ? lastFile.name : "drawing";

      if (balloonData) {
        // Data exists — try to save, but never block navigation on failure
        dashboardBtn.disabled = true;
        dashboardBtn.textContent = "Saving…";

        try {
          const resp = await fetch(ORIGIN + "/activities/save", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Authorization": "Bearer " + token,
            },
            body: JSON.stringify({
              filename: filename,
              drawing_preview_b64: previewB64,
              extracted_data: balloonData,
              excel_data: tableData,
            }),
          });

          if (resp.ok) {
            showToast("Session saved successfully", "success");
          } else {
            let detail = "Save failed (HTTP " + resp.status + ").";
            try { const j = await resp.json(); detail = j.detail || detail; } catch (_) {}
            console.error("[dashboard] Save error:", detail);
          }
        } catch (e) {
          console.error("[dashboard] Network error — could not save:", e);
        }
      } else {
        showToast("No data to save — redirecting to dashboard", "info");
      }

      // Always navigate to dashboard
      resetSession();
      setTimeout(function () {
        window.location.href = "http://localhost:3000/dashboard";
      }, 800);
    });
  }

  fetch(ORIGIN + "/api/auth/me", cred)
    .then(function (r) {
      return r.json();
    })
    .then(function (me) {
      if (me && me.ok && me.role === "admin" && adminLink) {
        adminLink.style.display = "inline-block";
      }
    })
    .catch(function () {});

  function syncJsonFromTable() {
    if (jsonOut && lastJson) jsonOut.textContent = JSON.stringify(lastJson, null, 2);
  }

  function downloadDataUrl(dataUrl, filename) {
    const a = document.createElement("a");
    a.href = dataUrl;
    a.download = filename;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  function safeCropFilenamePart(className) {
    var s = (className || "").trim();
    if (!s) return "class";
    s = s.replace(/[^a-zA-Z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
    return s.slice(0, 48) || "class";
  }

  function renderResultTable(data) {
    if (!resultBody) return;
    const items = ((data || {}).detection || {}).balloon_items || [];
    if (!items.length) {
      resultBody.innerHTML = "<tr><td colspan=\"5\">No extracted values found.</td></tr>";
      return;
    }
    resultBody.innerHTML = "";
    items.forEach(function (it, idx) {
      let nominal = it.nominal_value != null ? String(it.nominal_value) : "";
      let tol = it.tolerance != null ? String(it.tolerance) : "";
      let others = it.others != null ? String(it.others) : "";
      if (!nominal && !tol && !others && (it.detected_text || "").trim()) {
        others = String(it.detected_text);
      }
      const cls = (it.class_name || "").trim();
      const tr = document.createElement("tr");
      const tdNum = document.createElement("td");
      tdNum.textContent = it.balloon_number != null ? String(it.balloon_number) : "";
      const tdClass = document.createElement("td");
      tdClass.textContent = cls;
      const tdNom = document.createElement("td");
      tdNom.className = "col-nominal";
      const inpNom = document.createElement("input");
      inpNom.type = "text";
      inpNom.className = "table-text-input";
      inpNom.value = nominal;
      inpNom.placeholder = "empty";
      inpNom.addEventListener("input", function () {
        if (!lastJson || !lastJson.detection || !lastJson.detection.balloon_items[idx]) return;
        lastJson.detection.balloon_items[idx].nominal_value = inpNom.value;
        syncJsonFromTable();
      });
      tdNom.appendChild(inpNom);
      const tdTol = document.createElement("td");
      tdTol.className = "col-tolerance";
      const inpTol = document.createElement("input");
      inpTol.type = "text";
      inpTol.className = "table-text-input";
      inpTol.value = tol;
      inpTol.placeholder = "empty";
      inpTol.addEventListener("input", function () {
        if (!lastJson || !lastJson.detection || !lastJson.detection.balloon_items[idx]) return;
        lastJson.detection.balloon_items[idx].tolerance = inpTol.value;
        syncJsonFromTable();
      });
      tdTol.appendChild(inpTol);
      const tdOth = document.createElement("td");
      tdOth.className = "col-others";
      const stack = document.createElement("div");
      stack.className = "others-stack";
      const cropUrl = it.crop_preview_base64 || "";
      const saveUrl = it.crop_save_base64 || cropUrl;
      if (cropUrl || saveUrl) {
        const box = document.createElement("div");
        box.className = "bbox-crop-box";
        box.setAttribute("data-detect-class", cls || "unknown");
        const head = document.createElement("div");
        head.className = "bbox-crop-box-head";
        const lab = document.createElement("span");
        lab.className = "bbox-crop-class";
        lab.textContent = cls || "—";
        head.appendChild(lab);
        const sub = document.createElement("span");
        sub.className = "bbox-crop-sub";
        sub.textContent = "Bounding box crop";
        head.appendChild(sub);
        box.appendChild(head);
        const visual = document.createElement("div");
        visual.className = "bbox-crop-visual";
        visual.title =
          "Exact YOLO bounding box (full image pixels). Save uses full-res crop; thumbnail may be scaled.";
        const img = document.createElement("img");
        img.className = "crop-thumb";
        img.alt = "Bounding box crop";
        img.decoding = "async";
        img.loading = "lazy";
        img.onerror = function () {
          visual.innerHTML = "";
          visual.classList.add("bbox-crop-error");
          visual.textContent = "Could not display crop image.";
        };
        img.src = cropUrl || saveUrl;
        visual.appendChild(img);
        box.appendChild(visual);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-secondary crop-save";
        btn.textContent = "Save crop";
        btn.addEventListener("click", function () {
          const n = it.balloon_number != null ? String(it.balloon_number) : String(idx + 1);
          var part = safeCropFilenamePart(cls);
          var ext = saveUrl && saveUrl.indexOf("image/png") !== -1 ? ".png" : ".jpg";
          downloadDataUrl(saveUrl, "balloon_crop_" + n + "_" + part + ext);
        });
        box.appendChild(btn);
        stack.appendChild(box);
      }
      const inpOth = document.createElement("input");
      inpOth.type = "text";
      inpOth.className = "table-text-input others-text-input";
      inpOth.value = others;
      inpOth.placeholder = "empty";
      inpOth.addEventListener("input", function () {
        if (!lastJson || !lastJson.detection || !lastJson.detection.balloon_items[idx]) return;
        lastJson.detection.balloon_items[idx].others = inpOth.value;
        syncJsonFromTable();
      });
      stack.appendChild(inpOth);
      tdOth.appendChild(stack);
      tr.appendChild(tdNum);
      tr.appendChild(tdClass);
      tr.appendChild(tdNom);
      tr.appendChild(tdTol);
      tr.appendChild(tdOth);
      resultBody.appendChild(tr);
    });
  }

  function clearPanel(el, placeholder) {
    if (!el) return;
    el.innerHTML = "";
    if (placeholder) {
      const p = document.createElement("p");
      p.className = "placeholder";
      p.textContent = placeholder;
      el.appendChild(p);
    }
  }

  function revokeInputPdfPreview() {
    if (inputPdfPreviewUrl) {
      try {
        URL.revokeObjectURL(inputPdfPreviewUrl);
      } catch (_) {}
      inputPdfPreviewUrl = null;
    }
  }

  /** After detection, show the same raster the server used (PDF / downscaled images). */
  function showProcessedInputPreview(det) {
    if (!panelInput || !det || !det.preview_image_base64) return;
    revokeInputPdfPreview();
    clearPanel(panelInput);
    const img = document.createElement("img");
    img.alt = "Input (processed image used for detection)";
    img.src = det.preview_image_base64;
    panelInput.appendChild(img);
  }

  function showInputPreview(file) {
    revokeInputPdfPreview();
    clearPanel(panelInput);
    setInputDownloadEnabled(!!file);
    if (!file) return;
    if (file.type === "application/pdf" || /\.pdf$/i.test(file.name)) {
      try {
        inputPdfPreviewUrl = URL.createObjectURL(file);
        const iframe = document.createElement("iframe");
        iframe.className = "embed-pdf-preview";
        iframe.title = "PDF preview (page 1)";
        iframe.src = inputPdfPreviewUrl;
        panelInput.appendChild(iframe);
      } catch (e) {
        const p = document.createElement("p");
        p.className = "placeholder";
        p.textContent = "Could not open PDF preview in this browser. Run auto ballooning to see the rasterized drawing.";
        panelInput.appendChild(p);
      }
      return;
    }
    const img = document.createElement("img");
    img.alt = "Input";
    img.src = URL.createObjectURL(file);
    panelInput.appendChild(img);
  }

  function loadImageForDraw(det) {
    return new Promise(function (resolve, reject) {
      const img = new Image();
      if (det.preview_image_base64) {
        img.src = det.preview_image_base64;
      } else if (lastFile) {
        img.src = URL.createObjectURL(lastFile);
      } else {
        reject(new Error("No image source"));
        return;
      }
      img.onload = function () {
        resolve(img);
      };
      img.onerror = reject;
    });
  }

  function makeCanvasFromImage(img, det, drawFn) {
    const iw = Number(det.width) || img.naturalWidth;
    const ih = Number(det.height) || img.naturalHeight;
    const maxW = Math.min(1100, iw);
    const sx = maxW / iw;
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(iw * sx);
    canvas.height = Math.floor(ih * sx);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    drawFn(ctx, sx, det);
    return canvas;
  }

  /**
   * Redraw the current balloon canvas in place (same element + listeners).
   * Use this while dragging or drawing a create-rect so we do not replace the canvas
   * (replacing it dropped drag handlers and made Edit feel broken).
   */
  function repaintBalloonLayerOnly() {
    if (!lastBalloonCanvas || !balloonImageCache || !lastJson || !lastJson.detection) return;
    const det = mergeDetForDraw(lastJson.detection);
    const iw = Number(det.width) || balloonImageCache.naturalWidth;
    const maxW = Math.min(1100, iw);
    const sx = maxW / iw;
    const canvas = lastBalloonCanvas;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(balloonImageCache, 0, 0, canvas.width, canvas.height);
    drawDetectionsThenBalloons(ctx, sx, det);
  }

  function drawDetections(ctx, scale, det) {
    (det.detections || []).forEach(function (d) {
      const bb = d.bbox;
      if (!bb || bb.length < 4) return;
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.strokeRect(bb[0] * scale, bb[1] * scale, (bb[2] - bb[0]) * scale, (bb[3] - bb[1]) * scale);
    });
  }

  function drawDetectionsThenBalloons(ctx, scale, det) {
    drawDetections(ctx, scale, det);
    if (pendingRectOverlay) {
      const pr = pendingRectOverlay;
      const x1 = Math.min(pr.x1, pr.x2);
      const y1 = Math.min(pr.y1, pr.y2);
      const x2 = Math.max(pr.x1, pr.x2);
      const y2 = Math.max(pr.y1, pr.y2);
      ctx.strokeStyle = "#fbbf24";
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      ctx.setLineDash([]);
    }
    drawBalloons(ctx, scale, det);
  }

  function drawBalloons(ctx, scale, det) {
    ctx.canvas._balloonHitTest = [];
    const overrides = det._balloon_ui_overrides || {};
    const anns = det.drawing_annotations || [];
    const BALLOON_DIAMETER_MM = 5;
    const CSS_DPI = 96;
    const pxPerMm = CSS_DPI / 25.4;
    const r = (BALLOON_DIAMETER_MM * pxPerMm) / 2;
    const darkRed = "#8b0000";
    const placed = [];
    const w = ctx.canvas.width;
    const h = ctx.canvas.height;
    const laneGap = r * 2.6;
    const laneMargin = r + 8;
    const laneY = { left: laneMargin, right: laneMargin };
    let imageData = null;
    try {
      imageData = ctx.getImageData(0, 0, w, h).data;
    } catch (e) {
      imageData = null;
    }

    function clamp(v, lo, hi) {
      return Math.max(lo, Math.min(hi, v));
    }

    function chooseCenter(ann, x1, y1, x2, y2) {
      let cx = (x1 + x2) / 2;
      let cy = (y1 + y2) / 2;
      const t = ann.TextPos;
      if (Array.isArray(t) && t.length >= 2 && Number.isFinite(t[0]) && Number.isFinite(t[1])) {
        cx = t[0] * scale;
        cy = t[1] * scale;
      }
      // Keep the balloon inside canvas bounds.
      cx = clamp(cx, r + 1, w - r - 1);
      cy = clamp(cy, r + 1, h - r - 1);
      return { cx: cx, cy: cy };
    }

    function nudgeAway(cx, cy) {
      const minDist = r * 2.2;
      let tries = 0;
      while (tries < 24) {
        let moved = false;
        for (let i = 0; i < placed.length; i++) {
          const p = placed[i];
          const dx = cx - p.cx;
          const dy = cy - p.cy;
          const d = Math.hypot(dx, dy) || 0.0001;
          if (d < minDist) {
            const push = (minDist - d) + 1;
            cx += (dx / d) * push;
            cy += (dy / d) * push;
            cx = clamp(cx, r + 1, w - r - 1);
            cy = clamp(cy, r + 1, h - r - 1);
            moved = true;
          }
        }
        if (!moved) break;
        tries += 1;
      }
      return { cx: cx, cy: cy };
    }

    function getInkRatio(cx, cy) {
      if (!imageData) return 0;
      const rr = Math.max(2, Math.floor(r * 0.9));
      const step = 2;
      let dark = 0;
      let total = 0;
      for (let yy = -rr; yy <= rr; yy += step) {
        for (let xx = -rr; xx <= rr; xx += step) {
          if (xx * xx + yy * yy > rr * rr) continue;
          const px = Math.round(cx + xx);
          const py = Math.round(cy + yy);
          if (px < 0 || py < 0 || px >= w || py >= h) continue;
          const idx = (py * w + px) * 4;
          const rv = imageData[idx];
          const gv = imageData[idx + 1];
          const bv = imageData[idx + 2];
          // Count mostly dark drawing/text pixels.
          if (rv < 165 && gv < 165 && bv < 165) dark += 1;
          total += 1;
        }
      }
      return total > 0 ? dark / total : 0;
    }

    function overlapPenalty(cx, cy) {
      let pen = 0;
      const minDist = r * 2.2;
      for (let i = 0; i < placed.length; i++) {
        const p = placed[i];
        const d = Math.hypot(cx - p.cx, cy - p.cy);
        if (d < minDist) pen += (minDist - d) / minDist;
      }
      return pen;
    }

    function chooseBestNearby(cx, cy) {
      const baseStep = Math.max(10, Math.round(r * 2.2));
      const candidates = [[0, 0]];
      // Ring search in cardinal + diagonal directions.
      for (let ring = 1; ring <= 10; ring++) {
        const d = ring * baseStep;
        candidates.push([d, 0], [-d, 0], [0, -d], [0, d]);
        candidates.push([d, -d], [-d, -d], [d, d], [-d, d]);
        // Slight offsets so we can escape dense note areas.
        const s = Math.round(d * 0.5);
        candidates.push([d, s], [d, -s], [-d, s], [-d, -s], [s, d], [-s, d], [s, -d], [-s, -d]);
      }
      let best = { cx: cx, cy: cy };
      let bestScore = Number.POSITIVE_INFINITY;
      for (let i = 0; i < candidates.length; i++) {
        const dx = candidates[i][0];
        const dy = candidates[i][1];
        const tx = clamp(cx + dx, r + 1, w - r - 1);
        const ty = clamp(cy + dy, r + 1, h - r - 1);
        const ink = getInkRatio(tx, ty);
        const overlap = overlapPenalty(tx, ty);
        // Prefer white areas first, then avoid overlap, then smaller movement.
        const dist = Math.hypot(dx, dy);
        const score = ink * 22 + overlap * 7 + dist * 0.0015;
        if (score < bestScore) {
          bestScore = score;
          best = { cx: tx, cy: ty };
        }
        // Stop early when we find a clean white zone with no overlap.
        if (ink < 0.015 && overlap < 0.01) break;
      }
      return best;
    }

    function placeInSideLane(preferred) {
      const side = preferred.cx > w * 0.55 ? "right" : "left";
      const cx = side === "right" ? (w - laneMargin) : laneMargin;
      let cy = Math.max(preferred.cy, laneY[side]);
      cy = clamp(cy, laneMargin, h - laneMargin);
      laneY[side] = cy + laneGap;
      return { cx: cx, cy: cy };
    }

    function annReadingOrderKey(ann) {
      const bb = ann.BBox;
      if (bb && bb.length >= 4) {
        return { cy: Number(bb[1]), cx: Number(bb[0]) };
      }
      if (Array.isArray(ann.TextPos) && ann.TextPos.length >= 2) {
        return { cy: Number(ann.TextPos[1]), cx: Number(ann.TextPos[0]) };
      }
      return { cy: 0, cx: 0 };
    }

    const sorted = anns.slice().sort(function (a, b) {
      const ka = annReadingOrderKey(a);
      const kb = annReadingOrderKey(b);
      if (ka.cy !== kb.cy) return ka.cy - kb.cy;
      return ka.cx - kb.cx;
    });

    sorted.forEach(function (ann) {
      const bb = ann.BBox;
      if (!bb || bb.length < 4) return;
      const x1 = bb[0] * scale;
      const y1 = bb[1] * scale;
      const x2 = bb[2] * scale;
      const y2 = bb[3] * scale;
      const o = overrides[ann.id];
      let cx;
      let cy;
      if (o && Number.isFinite(o.cx) && Number.isFinite(o.cy)) {
        cx = clamp(o.cx, r + 1, w - r - 1);
        cy = clamp(o.cy, r + 1, h - r - 1);
      } else {
        const preferred = chooseCenter(ann, x1, y1, x2, y2);
        const whiteSpot = chooseBestNearby(preferred.cx, preferred.cy);
        const inkAtWhiteSpot = getInkRatio(whiteSpot.cx, whiteSpot.cy);
        const laneSpot = inkAtWhiteSpot > 0.055 ? placeInSideLane(preferred) : whiteSpot;
        const pos = nudgeAway(laneSpot.cx, laneSpot.cy);
        cx = pos.cx;
        cy = pos.cy;
      }

      ctx.save();
      ctx.strokeStyle = darkRed;
      ctx.fillStyle = darkRed;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.font = "bold 8px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(ann.id != null ? ann.id : ""), cx, cy);
      ctx.restore();
      placed.push({ cx: cx, cy: cy });
      const hitR = Math.max(22, r * 2.4);
      ctx.canvas._balloonHitTest.push({ id: ann.id, cx: cx, cy: cy, r: r, hitR: hitR });
    });
  }

  function mergeDetForDraw(det) {
    if (!det) return null;
    const d = Object.assign({}, det);
    const merged = Object.assign({}, det.balloon_ui_overrides || {}, balloonUiOverrides);
    if (Object.keys(merged).length) {
      d._balloon_ui_overrides = merged;
    }
    return d;
  }

  function balloonHitDistance(mx, my, h) {
    const maxD = h.hitR != null ? h.hitR : h.r + 8;
    return Math.hypot(mx - h.cx, my - h.cy) <= maxD;
  }

  function detachBalloonDragListeners() {
    document.removeEventListener("mousemove", onDocumentBalloonDragMove);
    document.removeEventListener("mouseup", onDocumentBalloonDragEnd);
  }

  function onDocumentBalloonDragMove(ev) {
    if (!activeBalloonDrag || !lastBalloonCanvas || !lastJson) return;
    const canvas = lastBalloonCanvas;
    const rect = canvas.getBoundingClientRect();
    const sx = canvas.width / rect.width;
    const mx = (ev.clientX - rect.left) * sx;
    const my = (ev.clientY - rect.top) * sx;
    balloonUiOverrides[activeBalloonDrag.id] = {
      cx: activeBalloonDrag.ox + (mx - activeBalloonDrag.startMx),
      cy: activeBalloonDrag.oy + (my - activeBalloonDrag.startMy),
    };
    repaintBalloonLayerOnly();
  }

  function onDocumentBalloonDragEnd() {
    if (!activeBalloonDrag) return;
    detachBalloonDragListeners();
    activeBalloonDrag = null;
    if (lastBalloonCanvas) lastBalloonCanvas.style.cursor = "default";
    if (lastJson && lastJson.detection) {
      lastJson.detection.balloon_ui_overrides = Object.assign({}, balloonUiOverrides);
      syncJsonFromTable();
    }
  }

  function attachCanvasInteractions(canvas) {
    canvas.addEventListener("mousedown", function (e) {
      if (balloonMode === "create") {
        const rect = canvas.getBoundingClientRect();
        const sc = canvas.width / rect.width;
        const x1 = (e.clientX - rect.left) * sc;
        const y1 = (e.clientY - rect.top) * sc;
        function onMove(ev) {
          const c = lastBalloonCanvas;
          if (!c) return;
          const r = c.getBoundingClientRect();
          const s = c.width / r.width;
          const x2 = (ev.clientX - r.left) * s;
          const y2 = (ev.clientY - r.top) * s;
          pendingRectOverlay = { x1: x1, y1: y1, x2: x2, y2: y2 };
          repaintBalloonLayerOnly();
        }
        function onUp(ev) {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          const c = lastBalloonCanvas;
          pendingRectOverlay = null;
          if (!c || !lastJson || !lastJson.detection) {
            paintBalloonCanvas();
            return;
          }
          const r = c.getBoundingClientRect();
          const s = c.width / r.width;
          const x2 = (ev.clientX - r.left) * s;
          const y2 = (ev.clientY - r.top) * s;
          if (Math.abs(x2 - x1) > 6 && Math.abs(y2 - y1) > 6) {
            addManualDetectionFromRect(c, x1, y1, x2, y2);
          } else {
            paintBalloonCanvas();
          }
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
        e.preventDefault();
        return;
      }

      if (balloonMode === "delete") {
        const hit = canvas._balloonHitTest;
        if (!hit || !hit.length) return;
        const rect = canvas.getBoundingClientRect();
        const sx = canvas.width / rect.width;
        const mx = (e.clientX - rect.left) * sx;
        const my = (e.clientY - rect.top) * sx;
        for (let i = hit.length - 1; i >= 0; i--) {
          const h = hit[i];
          if (balloonHitDistance(mx, my, h)) {
            deleteBalloonById(h.id);
            e.preventDefault();
            return;
          }
        }
        return;
      }

      if (balloonMode !== "edit") return;
      const hit = canvas._balloonHitTest;
      if (!hit || !hit.length) return;
      const rect = canvas.getBoundingClientRect();
      const sx = canvas.width / rect.width;
      const mx = (e.clientX - rect.left) * sx;
      const my = (e.clientY - rect.top) * sx;
      for (let i = hit.length - 1; i >= 0; i--) {
        const h = hit[i];
        if (balloonHitDistance(mx, my, h)) {
          const ov = balloonUiOverrides[h.id];
          const ox = ov && Number.isFinite(ov.cx) ? ov.cx : h.cx;
          const oy = ov && Number.isFinite(ov.cy) ? ov.cy : h.cy;
          activeBalloonDrag = { id: h.id, startMx: mx, startMy: my, ox: ox, oy: oy };
          canvas.style.cursor = "grabbing";
          document.addEventListener("mousemove", onDocumentBalloonDragMove);
          document.addEventListener("mouseup", onDocumentBalloonDragEnd);
          e.preventDefault();
          return;
        }
      }
    });

    canvas.addEventListener("mousemove", function (e) {
      if (activeBalloonDrag) return;
      if (balloonMode === "create") {
        canvas.style.cursor = "crosshair";
        return;
      }
      if (balloonMode === "delete") {
        const hit = canvas._balloonHitTest;
        if (!hit || !hit.length) {
          canvas.style.cursor = "default";
          return;
        }
        const rect = canvas.getBoundingClientRect();
        const sx = canvas.width / rect.width;
        const mx = (e.clientX - rect.left) * sx;
        const my = (e.clientY - rect.top) * sx;
        let over = false;
        for (let i = 0; i < hit.length; i++) {
          if (balloonHitDistance(mx, my, hit[i])) {
            over = true;
            break;
          }
        }
        canvas.style.cursor = over ? "not-allowed" : "default";
        return;
      }
      if (balloonMode !== "edit") {
        canvas.style.cursor = "default";
        return;
      }
      const hit = canvas._balloonHitTest;
      if (!hit || !hit.length) return;
      const rect = canvas.getBoundingClientRect();
      const sx = canvas.width / rect.width;
      const mx = (e.clientX - rect.left) * sx;
      const my = (e.clientY - rect.top) * sx;
      let over = false;
      for (let i = 0; i < hit.length; i++) {
        if (balloonHitDistance(mx, my, hit[i])) {
          over = true;
          break;
        }
      }
      canvas.style.cursor = over ? "grab" : "default";
    });

    canvas.addEventListener("mouseup", function () {
      /* drag end handled on document */
    });
  }

  function paintBalloonCanvas() {
    detachBalloonDragListeners();
    activeBalloonDrag = null;
    if (!balloonImageCache || !lastJson || !lastJson.detection) return;
    clearPanel(panelBalloon);
    const det = mergeDetForDraw(lastJson.detection);
    const balloonCanvas = makeCanvasFromImage(balloonImageCache, det, drawDetectionsThenBalloons);
    panelBalloon.appendChild(balloonCanvas);
    lastBalloonCanvas = balloonCanvas;
    attachCanvasInteractions(balloonCanvas);
    setBalloonDownloadEnabled(true);
  }

  function renderResults(data) {
    const det = data.detection;
    if (!det) return;
    balloonUiOverrides = Object.assign({}, det.balloon_ui_overrides || {});
    balloonMode = null;
    pendingRectOverlay = null;
    detachBalloonDragListeners();
    activeBalloonDrag = null;
    setBalloonModeButtons();

    loadImageForDraw(det)
      .then(function (img) {
        balloonImageCache = img;
        lastJson = data;
        setBalloonToolsEnabled(true);
        setModeHint(
          "Balloons are numbered top→bottom, left→right. Create / Edit / Delete, then Save."
        );
        paintBalloonCanvas();
        showProcessedInputPreview(det);

        if (!det.preview_image_base64 && lastFile && img.src.indexOf("blob:") === 0) {
          URL.revokeObjectURL(img.src);
        }
      })
      .catch(function (e) {
        clearPanel(panelBalloon, "Could not render: " + e);
        lastBalloonCanvas = null;
        balloonImageCache = null;
        setBalloonDownloadEnabled(false);
      });
  }

  function downloadBlob(blob, filename) {
    const a = document.createElement("a");
    const url = URL.createObjectURL(blob);
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 0);
  }

  function toCsvCell(v) {
    const s = v == null ? "" : String(v);
    if (/[",\n]/.test(s)) return "\"" + s.replace(/"/g, "\"\"") + "\"";
    return s;
  }

  function buildCsvFromJson(payload) {
    const det = (payload && payload.detection) || {};
    const rows = [];
    rows.push(["Summary"]);
    rows.push(["filename", payload.filename || ""]);
    rows.push(["count", det.count || 0]);
    rows.push(["width", det.width || ""]);
    rows.push(["height", det.height || ""]);
    rows.push([]);
    rows.push(["Detections"]);
    rows.push(["id", "class_name", "confidence", "x1", "y1", "x2", "y2"]);
    (det.detections || []).forEach(function (d, idx) {
      const bb = d.bbox || [];
      rows.push([
        idx + 1,
        d.class_name || "",
        d.confidence || "",
        bb[0] != null ? bb[0] : "",
        bb[1] != null ? bb[1] : "",
        bb[2] != null ? bb[2] : "",
        bb[3] != null ? bb[3] : "",
      ]);
    });
    rows.push([]);
    rows.push(["Balloons"]);
    rows.push(["id", "AnnotationType", "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2", "text_x", "text_y"]);
    (det.drawing_annotations || []).forEach(function (a) {
      const bb = a.BBox || [];
      const tp = a.TextPos || [];
      rows.push([
        a.id != null ? a.id : "",
        a.AnnotationType || "",
        bb[0] != null ? bb[0] : "",
        bb[1] != null ? bb[1] : "",
        bb[2] != null ? bb[2] : "",
        bb[3] != null ? bb[3] : "",
        tp[0] != null ? tp[0] : "",
        tp[1] != null ? tp[1] : "",
      ]);
    });
    rows.push([]);
    rows.push(["Extracted text (per balloon)"]);
    rows.push(["balloon_number", "class_name", "confidence", "nominal_value", "tolerance", "others"]);
    (det.balloon_items || []).forEach(function (it) {
      var oth = it.others != null ? String(it.others) : "";
      if (!oth && (it.detected_text || "").trim()) oth = String(it.detected_text);
      rows.push([
        it.balloon_number != null ? it.balloon_number : "",
        it.class_name || "",
        it.confidence != null ? it.confidence : "",
        it.nominal_value != null ? it.nominal_value : "",
        it.tolerance != null ? it.tolerance : "",
        oth,
      ]);
    });
    return rows.map(function (r) { return r.map(toCsvCell).join(","); }).join("\n");
  }

  if (downloadInput) {
    downloadInput.addEventListener("click", function () {
      if (!lastFile) return;
      downloadBlob(lastFile, lastFile.name || "input");
    });
  }

  if (downloadBalloon) {
    downloadBalloon.addEventListener("click", function () {
      if (!lastBalloonCanvas) return;
      lastBalloonCanvas.toBlob(function (blob) {
        if (!blob) return;
        const base = lastFile && lastFile.name ? lastFile.name.replace(/\.[^.]+$/, "") : "drawing";
        downloadBlob(blob, "AutoBallooning_" + base + ".png");
      }, "image/png");
    });
  }

  if (downloadExcel) {
    downloadExcel.addEventListener("click", async function () {
      if (!lastJson) return;
      try {
        const paths = ["/api/v1/export-excel", "/api/export-excel", "/export-excel"];
        let r = null;
        for (let i = 0; i < paths.length; i++) {
          const _excelHdr = { "Content-Type": "application/json" };
          const _excelTok = getAuthToken(); if (_excelTok) _excelHdr["Authorization"] = "Bearer " + _excelTok;
          const rr = await fetch(ORIGIN + paths[i], Object.assign({
            method: "POST",
            headers: _excelHdr,
            body: JSON.stringify(lastJson),
          }, cred));
          if (authRedirect(rr.status)) return;
          if (rr.ok) {
            r = rr;
            break;
          }
          if (rr.status !== 404) {
            r = rr;
            break;
          }
        }
        if (!r || !r.ok) {
          const base404 = !r || r.status === 404;
          if (base404) {
            const base = lastFile && lastFile.name ? lastFile.name.replace(/\.[^.]+$/, "") : "drawing";
            const csv = buildCsvFromJson(lastJson);
            const blobCsv = new Blob([csv], { type: "text/csv;charset=utf-8" });
            downloadBlob(blobCsv, "AutoBallooning_" + base + ".csv");
            setStatus("Excel endpoint missing; downloaded CSV for Excel.");
            return;
          }
          setStatus("Excel export failed: HTTP " + r.status);
          return;
        }
        const blob = await r.blob();
        const base = lastFile && lastFile.name ? lastFile.name.replace(/\.[^.]+$/, "") : "drawing";
        downloadBlob(blob, "AutoBallooning_" + base + ".xlsx");
      } catch (e) {
        setStatus("Excel export failed: " + e);
      }
    });
  }

  if (inspectionReport) {
    inspectionReport.addEventListener("click", function () {
      if (!lastJson) return;
      try {
        sessionStorage.setItem(INSPECTION_STORAGE_KEY, JSON.stringify(lastJson));
      } catch (e) {
        setStatus("Could not open inspection report: " + e);
        return;
      }
      window.location.href = "/inspection-report";
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", function () {
      const newFile = fileInput.files && fileInput.files[0];
      resetSession();
      lastFile = newFile;
      showInputPreview(newFile);
    });
  }

  if (btnBalloonMenu) {
    btnBalloonMenu.addEventListener("click", function (e) {
      e.stopPropagation();
      if (btnBalloonMenu.disabled) return;
      setQuickPanelOpen(!!balloonQuickPanel.hidden);
    });
  }
  if (btnCloseQuickPanel) {
    btnCloseQuickPanel.addEventListener("click", function () {
      setQuickPanelOpen(false);
    });
  }
  if (btnQuickSave) {
    btnQuickSave.addEventListener("click", function (e) {
      e.stopPropagation();
      applyQuickTableToDetection();
    });
  }
  if (btnCloseCropModal) {
    btnCloseCropModal.addEventListener("click", function (e) {
      e.stopPropagation();
      closeQuickCropModal();
    });
  }
  if (quickCropModalBackdrop) {
    quickCropModalBackdrop.addEventListener("click", function (e) {
      e.stopPropagation();
      closeQuickCropModal();
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && quickCropViewModal && !quickCropViewModal.hidden) {
      closeQuickCropModal();
    }
  });
  document.addEventListener("click", function (e) {
    if (!balloonQuickPanel || balloonQuickPanel.hidden) return;
    if (e.target.closest && e.target.closest("#balloonQuickPanel")) return;
    if (e.target.closest && e.target.closest(".balloon-menu-wrap")) return;
    if (e.target.closest && e.target.closest("#quickCropViewModal")) return;
    setQuickPanelOpen(false);
  });

  if (btnModeCreate) {
    btnModeCreate.addEventListener("click", function () {
      balloonMode = balloonMode === "create" ? null : "create";
      pendingRectOverlay = null;
      setBalloonModeButtons();
      setModeHint(
        balloonMode === "create"
          ? "Create: drag on the drawing to draw a rectangle, then release."
          : ""
      );
      paintBalloonCanvas();
    });
  }
  if (btnModeEdit) {
    btnModeEdit.addEventListener("click", function () {
      balloonMode = balloonMode === "edit" ? null : "edit";
      setBalloonModeButtons();
      setModeHint(balloonMode === "edit" ? "Edit: drag red balloon circles to move them (works even if the cursor leaves the image)." : "");
      paintBalloonCanvas();
    });
  }
  if (btnModeDelete) {
    btnModeDelete.addEventListener("click", function () {
      balloonMode = balloonMode === "delete" ? null : "delete";
      setBalloonModeButtons();
      setModeHint(
        balloonMode === "delete"
          ? "Delete: click a red balloon to remove it. Remaining balloons are renumbered. Then Save."
          : ""
      );
      paintBalloonCanvas();
    });
  }
  if (btnModeSave) {
    btnModeSave.addEventListener("click", function () {
      applyBalloonSave();
    });
  }

  if (runBtn) {
  runBtn.addEventListener("click", async function () {
    if (!lastFile) {
      setStatus("Choose a file first.");
      return;
    }
    runBtn.disabled = true;
    setStatus("Processing…");
    if (jsonOut) jsonOut.textContent = "…";

    try {
      const fd = new FormData();
      fd.append("file", lastFile);
      const _detectHeaders = {};
      const _detectToken = getAuthToken();
      if (_detectToken) _detectHeaders["Authorization"] = "Bearer " + _detectToken;
      const r = await fetch(ORIGIN + "/api/v1/detect", Object.assign({
        method: "POST",
        headers: _detectHeaders,
        body: fd,
      }, cred));
      if (authRedirect(r.status)) return;
      const text = await r.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch (e) {
        setStatus("Non-JSON response HTTP " + r.status);
        if (jsonOut) jsonOut.textContent = text.slice(0, 4000);
        return;
      }
      if (jsonOut) jsonOut.textContent = JSON.stringify(data, null, 2);
      if (!r.ok || !data.ok) {
        setStatus("Error: " + (data.error || data.detail || "HTTP " + r.status));
        return;
      }
      lastJson = data;
      renumberBalloonsReadingOrder();
      renderResults(data);
      renderResultTable(data);
      setExcelDownloadEnabled(true);
      setInspectionReportEnabled(true);
      setStatus("Done.");
    } catch (e) {
      setStatus("Request failed: " + e);
      if (jsonOut) jsonOut.textContent = String(e);
    }
    runBtn.disabled = false;
  });
  }
})();
