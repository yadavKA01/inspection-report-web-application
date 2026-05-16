(function () {
  const STORAGE_KEY = "smorx_inspection_payload";
  const META_KEY = "smorx_inspection_meta";
  const MIN_MEASURED_COLS = 1;
  const MAX_MEASURED_COLS = 6;
  const DEFAULT_MEASURED_COLS = 3;

  const irEmpty = document.getElementById("irEmpty");
  const irContent = document.getElementById("irContent");
  const irThead = document.getElementById("irThead");
  const irTbody = document.getElementById("irTbody");
  const irDashboardBtn = document.getElementById("irDashboardBtn");
  const irTableWrap = document.getElementById("irTableWrap");
  const irScrollLeft = document.getElementById("irScrollLeft");
  const irScrollRight = document.getElementById("irScrollRight");

  let measuredColCount = DEFAULT_MEASURED_COLS;
  let rows = [];

  function parseTolOffset(val) {
    if (val === "" || val === "—" || val == null) return null;
    const s = String(val).trim().replace(/^\+/, "");
    const n = parseFloat(s);
    return Number.isFinite(n) ? n : null;
  }

  function parseDetectedText(raw) {
    const text = (raw || "").trim();
    if (!text) return { nominal: "", tolLow: "", tolHigh: "" };

    let s = text.replace(/^[ØøΦφ]\s*/i, "").replace(/\s+/g, " ");

    let m = s.match(/^([+-]?\d+\.?\d*)\s*[±]\s*(\d+\.?\d*)/);
    if (m) {
      const tol = parseFloat(m[2]);
      return { nominal: m[1], tolLow: String(-tol), tolHigh: String(tol) };
    }

    m = s.match(/^([+-]?\d+\.?\d*)\s*([+-]\d+\.?\d*)\s*\/\s*([+-]\d+\.?\d*)/);
    if (m) {
      return { nominal: m[1], tolLow: m[3], tolHigh: m[2].replace(/^\+/, "") };
    }

    m = s.match(/^([+-]?\d+\.?\d*)\s*\+\s*(\d+\.?\d*)\s*\/\s*-?\s*(\d+\.?\d*)/);
    if (m) {
      return { nominal: m[1], tolLow: "-" + m[3], tolHigh: m[2] };
    }

    m = s.match(/^([+-]?\d+\.?\d*)\s*([+-]\d+\.?\d*)\s+([+-]\d+\.?\d*)/);
    if (m) {
      return { nominal: m[1], tolLow: m[3], tolHigh: m[2].replace(/^\+/, "") };
    }

    m = s.match(/^([+-]?\d+\.?\d*)\s*$/);
    if (m) return { nominal: m[1], tolLow: "", tolHigh: "" };

    m = s.match(/([+-]?\d+\.?\d*)/);
    if (m) return { nominal: m[1], tolLow: "", tolHigh: "" };

    return { nominal: text, tolLow: "", tolHigh: "" };
  }

  function referenceFromAnnotation(ann, imgW, imgH) {
    if (!ann || !ann.BBox || ann.BBox.length < 4 || !imgW || !imgH) return "";
    const bb = ann.BBox;
    const cx = (bb[0] + bb[2]) / 2;
    const cy = (bb[1] + bb[3]) / 2;
    const cols = 10;
    const rowSlots = 10;
    const col = Math.min(cols, Math.max(1, Math.ceil((cx / imgW) * cols)));
    const rowIdx = Math.min(rowSlots, Math.max(1, Math.ceil((cy / imgH) * rowSlots)));
    const rowLetter = String.fromCharCode(64 + rowIdx);
    return "Col " + col + " - Row " + rowLetter;
  }

  function formatTolDisplay(val) {
    if (val === "" || val == null) return "—";
    return String(val);
  }

  function evaluateMeasurement(nominal, tolLow, tolHigh, measured) {
    const mStr = String(measured == null ? "" : measured).trim();
    if (!mStr) return { status: "none", label: "—" };

    const m = parseFloat(mStr);
    if (!Number.isFinite(m)) return { status: "none", label: "—" };

    const n = parseFloat(String(nominal).trim());
    if (!Number.isFinite(n)) return { status: "none", label: "—" };

    const lo = parseTolOffset(tolLow);
    const hi = parseTolOffset(tolHigh);
    const hasLo = lo !== null;
    const hasHi = hi !== null;

    if (!hasLo && !hasHi) {
      if (Math.abs(m - n) < 1e-6) return { status: "pass", label: "Pass" };
      return { status: "fail", label: "Fail" };
    }

    const lowerBound = hasLo ? n + lo : n;
    const upperBound = hasHi ? n + hi : n;
    const eps = 1e-6;

    if (m < lowerBound - eps || m > upperBound + eps) return { status: "fail", label: "Fail" };
    if (Math.abs(m - lowerBound) < eps || Math.abs(m - upperBound) < eps) {
      return { status: "warn", label: "Pass" };
    }
    return { status: "pass", label: "Pass" };
  }

  function measuredCellHtml(row, rowIdx, col) {
    const ev = evaluateMeasurement(row.nominal, row.tolLow, row.tolHigh, row.measured[col]);
    const cls = ev.status === "pass" ? "ir-pass" : ev.status === "warn" ? "ir-warn" : ev.status === "fail" ? "ir-fail" : "";
    const dotCls = ev.status === "none" ? "" : ev.status;
    return (
      '<td><div class="ir-measured-cell">' +
      '<span class="ir-status-dot ' + dotCls + '" data-dot-row="' + rowIdx + '" data-dot-col="' + col + '"></span>' +
      '<input type="text" class="ir-measured ' + cls + '" data-field="measured" data-row="' + rowIdx + '" data-col="' + col + '" value="' + escAttr(row.measured[col]) + '" />' +
      "</div></td>"
    );
  }

  function buildRowsFromPayload(payload) {
    const det = (payload && payload.detection) || {};
    const items = det.balloon_items || [];
    const anns = det.drawing_annotations || [];
    const imgW = Number(det.width) || 1;
    const imgH = Number(det.height) || 1;
    const annById = {};
    anns.forEach(function (a) {
      if (a.id != null) annById[a.id] = a;
    });

    return items.map(function (it, idx) {
      const bn = it.balloon_number != null ? it.balloon_number : idx + 1;
      const parsed = parseDetectedText(it.detected_text || "");
      const ann = annById[bn] || anns[idx];
      const measured = [];
      for (let i = 0; i < measuredColCount; i++) measured.push("");
      return {
        sno: idx + 1,
        balloonNumber: bn,
        referenceLocation: referenceFromAnnotation(ann, imgW, imgH),
        nominal: parsed.nominal,
        tolLow: parsed.tolLow,
        tolHigh: parsed.tolHigh,
        instrument: "",
        instrumentId: "",
        measured: measured,
        remarks: "",
      };
    });
  }

  function loadMeta() {
    try {
      const raw = sessionStorage.getItem(META_KEY);
      if (!raw) return;
      const meta = JSON.parse(raw);
      if (meta.partNumber != null) document.getElementById("irPartNumber").value = meta.partNumber;
      if (meta.partName != null) document.getElementById("irPartName").value = meta.partName;
      if (meta.revision != null) document.getElementById("irRevision").value = meta.revision;
      if (meta.material != null) document.getElementById("irMaterial").value = meta.material;
      if (meta.mass != null) document.getElementById("irMass").value = meta.mass;
      if (meta.finish != null) document.getElementById("irFinish").value = meta.finish;
      if (meta.measuredColCount != null) measuredColCount = meta.measuredColCount;
    } catch (e) { /* ignore */ }
  }

  function saveMeta() {
    const meta = {
      partNumber: document.getElementById("irPartNumber").value,
      partName: document.getElementById("irPartName").value,
      revision: document.getElementById("irRevision").value,
      material: document.getElementById("irMaterial").value,
      mass: document.getElementById("irMass").value,
      finish: document.getElementById("irFinish").value,
      measuredColCount: measuredColCount,
    };
    sessionStorage.setItem(META_KEY, JSON.stringify(meta));
  }

  function colCtrlHeaderHtml() {
    return (
      '<th class="ir-th-colctrl" rowspan="2">' +
      '<div class="ir-colctrl-btns">' +
      '<button type="button" id="irAddCol">+ column</button>' +
      '<button type="button" id="irRemoveCol">− column</button>' +
      "</div></th>"
    );
  }

  function renderHeader() {
    const accSpan = 8;
    const measSpan = measuredColCount + 2;
    let html =
      "<tr>" +
      '<th colspan="' + accSpan + '" class="ir-col-accountability">Characteristics Accountability</th>' +
      '<th colspan="' + measSpan + '">Inspection &amp; results</th>' +
      "</tr>" +
      "<tr>" +
      "<th>S.No</th>" +
      "<th>Balloon Number</th>" +
      "<th>Reference location</th>" +
      "<th>Nominal</th>" +
      "<th>Tol (low)</th>" +
      "<th>Tol (high)</th>" +
      "<th>Instrument</th>" +
      '<th class="ir-col-accountability">Instrument ID</th>';

    for (let c = 0; c < measuredColCount; c++) {
      html += '<th class="ir-th-measured">Measured ' + (c + 1) + "</th>";
    }
    html += colCtrlHeaderHtml();
    html += "<th>Remarks</th></tr>";
    irThead.innerHTML = html;

    const addBtn = document.getElementById("irAddCol");
    const remBtn = document.getElementById("irRemoveCol");
    if (addBtn) {
      addBtn.disabled = measuredColCount >= MAX_MEASURED_COLS;
      addBtn.onclick = addMeasuredColumn;
    }
    if (remBtn) {
      remBtn.disabled = measuredColCount <= MIN_MEASURED_COLS;
      remBtn.onclick = removeMeasuredColumn;
    }
  }

  function renderBody() {
    irTbody.innerHTML = "";
    rows.forEach(function (row, rowIdx) {
      while (row.measured.length < measuredColCount) row.measured.push("");
      if (row.measured.length > measuredColCount) row.measured = row.measured.slice(0, measuredColCount);

      const tr = document.createElement("tr");
      let html =
        '<td><span class="ir-readonly">' + row.sno + "</span></td>" +
        '<td><span class="ir-readonly">' + row.balloonNumber + "</span></td>" +
        '<td class="ir-td-ref"><input type="text" data-field="reference" data-row="' + rowIdx + '" value="' + escAttr(row.referenceLocation) + '" /></td>' +
        '<td><span class="ir-readonly">' + escHtml(row.nominal || "—") + "</span></td>" +
        '<td><span class="ir-readonly">' + escHtml(formatTolDisplay(row.tolLow)) + "</span></td>" +
        '<td><span class="ir-readonly">' + escHtml(formatTolDisplay(row.tolHigh)) + "</span></td>" +
        '<td><input type="text" data-field="instrument" data-row="' + rowIdx + '" value="' + escAttr(row.instrument) + '" /></td>' +
        '<td><input type="text" data-field="instrumentId" data-row="' + rowIdx + '" value="' + escAttr(row.instrumentId) + '" /></td>';

      for (let c = 0; c < measuredColCount; c++) {
        html += measuredCellHtml(row, rowIdx, c);
      }
      html +=
        '<td class="ir-th-colctrl"></td>' +
        '<td class="ir-td-remarks"><input type="text" data-field="remarks" data-row="' + rowIdx + '" value="' + escAttr(row.remarks) + '" /></td>';

      tr.innerHTML = html;
      irTbody.appendChild(tr);
    });

    bindRowInputs();
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escAttr(s) {
    return escHtml(s).replace(/'/g, "&#39;");
  }

  function updateMeasuredVisual(rowIdx, col) {
    const tr = irTbody.children[rowIdx];
    if (!tr || !rows[rowIdx]) return;
    const row = rows[rowIdx];
    const ev = evaluateMeasurement(row.nominal, row.tolLow, row.tolHigh, row.measured[col]);
    const inp = tr.querySelector('input[data-field="measured"][data-row="' + rowIdx + '"][data-col="' + col + '"]');
    const dot = tr.querySelector('.ir-status-dot[data-dot-row="' + rowIdx + '"][data-dot-col="' + col + '"]');
    if (inp) {
      inp.classList.remove("ir-pass", "ir-warn", "ir-fail");
      if (ev.status === "pass") inp.classList.add("ir-pass");
      else if (ev.status === "warn") inp.classList.add("ir-warn");
      else if (ev.status === "fail") inp.classList.add("ir-fail");
    }
    if (dot) {
      dot.classList.remove("pass", "warn", "fail");
      if (ev.status !== "none") dot.classList.add(ev.status);
    }
  }

  function bindRowInputs() {
    irTbody.querySelectorAll("input").forEach(function (inp) {
      inp.addEventListener("input", onRowInput);
      inp.addEventListener("change", onRowInput);
    });
  }

  function onRowInput(ev) {
    const inp = ev.target;
    const rowIdx = parseInt(inp.getAttribute("data-row"), 10);
    const field = inp.getAttribute("data-field");
    if (!Number.isFinite(rowIdx) || !rows[rowIdx]) return;
    const row = rows[rowIdx];

    if (field === "reference") row.referenceLocation = inp.value;
    else if (field === "instrument") row.instrument = inp.value;
    else if (field === "instrumentId") row.instrumentId = inp.value;
    else if (field === "remarks") row.remarks = inp.value;
    else if (field === "measured") {
      const col = parseInt(inp.getAttribute("data-col"), 10);
      row.measured[col] = inp.value;
      updateMeasuredVisual(rowIdx, col);
    }
    saveMeta();
  }

  function renderTable() {
    renderHeader();
    renderBody();
  }

  function addMeasuredColumn() {
    if (measuredColCount >= MAX_MEASURED_COLS) return;
    measuredColCount += 1;
    rows.forEach(function (r) {
      r.measured.push("");
    });
    saveMeta();
    renderTable();
  }

  function removeMeasuredColumn() {
    if (measuredColCount <= MIN_MEASURED_COLS) return;
    measuredColCount -= 1;
    rows.forEach(function (r) {
      if (r.measured.length > measuredColCount) r.measured = r.measured.slice(0, measuredColCount);
    });
    saveMeta();
    renderTable();
  }

  function init() {
    let payload = null;
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) payload = JSON.parse(raw);
    } catch (e) {
      payload = null;
    }

    if (!payload || !payload.detection) {
      irEmpty.hidden = false;
      irContent.hidden = true;
      return;
    }

    irEmpty.hidden = true;
    irContent.hidden = false;
    loadMeta();
    if (!measuredColCount || measuredColCount < MIN_MEASURED_COLS) {
      measuredColCount = DEFAULT_MEASURED_COLS;
    }
    rows = buildRowsFromPayload(payload);
    renderTable();

    ["irPartNumber", "irPartName", "irRevision", "irMaterial", "irMass", "irFinish"].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) el.addEventListener("input", saveMeta);
    });
  }

  if (irDashboardBtn) {
    irDashboardBtn.addEventListener("click", function () {
      saveMeta();
    });
  }

  if (irScrollLeft && irTableWrap) {
    irScrollLeft.addEventListener("click", function () {
      irTableWrap.scrollBy({ left: -280, behavior: "smooth" });
    });
  }
  if (irScrollRight && irTableWrap) {
    irScrollRight.addEventListener("click", function () {
      irTableWrap.scrollBy({ left: 280, behavior: "smooth" });
    });
  }

  init();
})();
