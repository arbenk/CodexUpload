(function () {
  "use strict";
  var state = { fonts: [], installedFonts: [], localizedNames: {}, selected: null, selectedTarget: null, navigationIndex: -1, optionIndex: -1, busy: false };
  function $(id) { return document.getElementById(id); }
  function hostCall(expression) {
    return new Promise(function (resolve, reject) {
      if (!window.__adobe_cep__) { reject(new Error("CEP 宿主接口不可用，请从 Photoshop 的扩展菜单打开本面板。")); return; }
      window.__adobe_cep__.evalScript(expression, function (value) {
        if (value === "EvalScript error.") { reject(new Error("Photoshop 脚本执行失败。")); return; }
        try {
          var result = JSON.parse(value);
          if (!result.ok) reject(new Error(result.error || "未知错误")); else resolve(result.data);
        } catch (error) { reject(new Error("宿主返回了无效数据：" + value)); }
      });
    });
  }
  function quote(value) { return JSON.stringify(String(value)); }
  function readableFontName(family, style, fallback) {
    family = String(family || fallback || ""); style = String(style || "");
    if (!style || family.toLowerCase().indexOf(style.toLowerCase()) !== -1) return family;
    return family + " " + style;
  }
  function localizeFont(font) {
    var localized = state.localizedNames[font.postScriptName];
    if (!localized) return font;
    font.displayName = readableFontName(localized.family, localized.style, font.displayName || font.postScriptName);
    font.localizedFamily = localized.family; font.localizedStyle = localized.style;
    return font;
  }
  function sortFonts(fonts) {
    function group(name) {
      var first = String(name || "").charAt(0);
      if (/[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/.test(first)) return 0;
      if (/[A-Za-z]/.test(first)) return 1;
      return 2;
    }
    return fonts.sort(function (a, b) {
      var an = a.displayName || a.family || a.postScriptName, bn = b.displayName || b.family || b.postScriptName;
      var difference = group(an) - group(bn);
      if (difference) return difference;
      an = an.toLowerCase(); bn = bn.toLowerCase();
      return an === bn ? 0 : (an < bn ? -1 : 1);
    });
  }
  function status(message, error) { $("status").textContent = message; $("status").classList.toggle("error", !!error); }
  function controls() {
    var item = selectedRecord();
    var enabled = !state.busy && item && item.layers.length;
    $("previousButton").disabled = !enabled; $("nextButton").disabled = !enabled;
    var fontInputDisabled = state.busy || !state.installedFonts.length;
    $("targetFont").disabled = fontInputDisabled; $("fontDropdownButton").disabled = fontInputDisabled;
    $("replaceButton").disabled = !enabled || !state.selectedTarget;
    $("scaleTextButton").disabled = state.busy;
    $("fontSizeMultiplier").disabled = state.busy;
    $("leadingMultiplier").disabled = state.busy;
    $("trackingMultiplier").disabled = state.busy;
    $("refreshButton").disabled = state.busy;
    $("customZoom").disabled = state.busy || !$("followView").checked;
    $("followZoom").disabled = state.busy || !$("followView").checked || !$("customZoom").checked;
  }
  function selectedRecord() { return state.fonts.filter(function (f) { return f.postScriptName === state.selected; })[0] || null; }
  function setBusy(value) { state.busy = value; controls(); }
  function renderFonts() {
    var list = $("fontList"); list.innerHTML = ""; $("fontCount").textContent = state.fonts.length;
    if (!state.fonts.length) { list.innerHTML = '<div class="empty">文档中没有文字图层</div>'; return; }
    state.fonts.forEach(function (font) {
      var button = document.createElement("button"); button.className = "font-item" + (font.postScriptName === state.selected ? " selected" : "");
      var names = document.createElement("span"); names.className = "font-names";
      var main = document.createElement("span"); main.className = "font-main"; main.textContent = font.displayName || (font.family + (font.style ? " " + font.style : ""));
      var fileName = document.createElement("span"); fileName.className = "font-file-name"; fileName.textContent = font.postScriptName;
      var usage = document.createElement("span"); usage.className = "font-usage"; usage.textContent = font.layers.length + " 层";
      names.appendChild(main); names.appendChild(fileName); button.appendChild(names); button.appendChild(usage);
      if (font.missing) {
        var svgNS = "http://www.w3.org/2000/svg", warning = document.createElementNS(svgNS, "svg"), triangle = document.createElementNS(svgNS, "path"), mark = document.createElementNS(svgNS, "path");
        warning.setAttribute("viewBox", "0 0 12 11"); warning.setAttribute("class", "missing-warning"); warning.setAttribute("role", "img"); warning.setAttribute("aria-label", "字体缺失");
        triangle.setAttribute("d", "M5.1 1.15a1.05 1.05 0 0 1 1.8 0l4.65 7.75a1.04 1.04 0 0 1-.9 1.57H1.35a1.04 1.04 0 0 1-.9-1.57z"); triangle.setAttribute("fill", "#f6c344"); triangle.setAttribute("stroke", "#171717"); triangle.setAttribute("stroke-width", ".65"); triangle.setAttribute("stroke-linejoin", "round");
        mark.setAttribute("d", "M5.45 3.25h1.1l-.14 3.7h-.82zm.02 4.55h1.06v1.05H5.47z"); mark.setAttribute("fill", "#242000");
        warning.appendChild(triangle); warning.appendChild(mark); button.appendChild(warning);
      }
      button.onclick = function () { state.selected = font.postScriptName; state.navigationIndex = -1; renderFonts(); controls(); };
      list.appendChild(button);
    });
  }
  function closeFontDropdown() { $("fontDropdown").hidden = true; state.optionIndex = -1; }
  function chooseTarget(font) {
    state.selectedTarget = font.postScriptName; $("targetFont").value = font.displayName || font.family || font.postScriptName;
    closeFontDropdown(); controls();
  }
  function renderFontOptions(query) {
    var dropdown = $("fontDropdown"), needle = String(query || "").toLowerCase(); dropdown.innerHTML = ""; state.optionIndex = -1;
    var matches = state.installedFonts.filter(function (font) { return (font.displayName || font.family || font.postScriptName).toLowerCase().indexOf(needle) !== -1; });
    if (!matches.length) { dropdown.innerHTML = '<div class="font-no-match">没有匹配的字体</div>'; }
    matches.forEach(function (font) {
      var option = document.createElement("button"); option.type = "button"; option.className = "font-option";
      option.textContent = font.displayName || font.family || font.postScriptName; option.onclick = function () { chooseTarget(font); };
      dropdown.appendChild(option);
    });
    dropdown.hidden = false;
  }
  function moveFontOption(delta) {
    var options = $("fontDropdown").querySelectorAll(".font-option"); if (!options.length) return;
    state.optionIndex = (state.optionIndex + delta + options.length) % options.length;
    for (var i = 0; i < options.length; i++) options[i].classList.toggle("active", i === state.optionIndex);
    options[state.optionIndex].scrollIntoView(false);
  }
  async function refresh() {
    setBusy(true); status("正在扫描文档…");
    try {
      var result = await hostCall("FontNavigator.scanDocument()"); state.fonts = result.fonts.map(localizeFont); state.selected = null; state.navigationIndex = -1;
      renderFonts(); status("扫描完成，共发现 " + result.fonts.length + " 种字体。");
    } catch (error) { state.fonts = []; renderFonts(); status(error.message, true); } finally { setBusy(false); }
  }
  async function navigate(delta) {
    var record = selectedRecord(); if (!record) return;
    var useCustomZoom = $("followView").checked && $("customZoom").checked, zoomPercent = useCustomZoom ? Number($("followZoom").value) : 0;
    if (useCustomZoom && (!isFinite(zoomPercent) || zoomPercent < 1 || zoomPercent > 3200)) { status("请输入 1–3200 之间的缩放比例。", true); return; }
    state.navigationIndex = (state.navigationIndex + delta + record.layers.length) % record.layers.length;
    try {
      var followed = $("followView").checked, result = await hostCall("FontNavigator.selectLayer(" + record.layers[state.navigationIndex].id + "," + (followed ? "true" : "false") + "," + zoomPercent + ")");
      var message = "已定位：" + record.layers[state.navigationIndex].name + "（" + (state.navigationIndex + 1) + "/" + record.layers.length + "）";
      if (followed && !result.viewFollowed) message += "；视图跟随不可用";
      else if (followed && useCustomZoom && !result.zoomApplied) message += "；自定义缩放不可用";
      status(message, followed && (!result.viewFollowed || (useCustomZoom && !result.zoomApplied)));
    }
    catch (error) { status(error.message, true); }
  }
  async function loadFonts() {
    try {
      state.localizedNames = window.FontNameLocalizer ? window.FontNameLocalizer.load() : {};
      state.installedFonts = sortFonts((await hostCall("FontNavigator.getInstalledFonts()")).map(localizeFont)); $("targetFont").placeholder = "输入或选择目标字体…";
    } catch (error) { status(error.message, true); } finally { controls(); }
  }
  async function replaceFont() {
    var record = selectedRecord(), target = state.selectedTarget; if (!record || !target) return;
    if (!window.confirm("把文档中的“" + (record.displayName || record.family) + "”全部替换为所选字体？")) return;
    setBusy(true); status("正在批量替换…");
    try { var result = await hostCall("FontNavigator.replaceFont(" + quote(record.postScriptName) + "," + quote(target) + ")"); status("替换完成，已更新 " + result.changedLayers + " 个图层。"); await refresh(); }
    catch (error) { status(error.message, true); } finally { setBusy(false); }
  }
  function multiplier(id, label) {
    var value = Number($(id).value);
    if (!isFinite(value) || value <= 0 || value > 100) throw new Error(label + "倍数必须大于 0 且不超过 100。");
    return value;
  }
  async function scaleText() {
    var size, leading, tracking;
    try {
      size = multiplier("fontSizeMultiplier", "字号");
      leading = multiplier("leadingMultiplier", "行距");
      tracking = multiplier("trackingMultiplier", "字距");
    } catch (error) { status(error.message, true); return; }
    if (size === 1 && leading === 1 && tracking === 1) { status("三个倍数都是 1，没有需要修改的属性。", true); return; }
    if (!window.confirm("把当前文档全部文字图层的字号、行距和字距按输入倍数修改？")) return;
    setBusy(true); status("正在批量缩放文字属性…");
    try {
      var result = await hostCall("FontNavigator.scaleTextProperties(" + size + "," + leading + "," + tracking + ")");
      status("缩放完成，已更新 " + result.changedLayers + " 个文字图层。跳过 " + result.skippedProperties + " 个无可缩放当前值的属性。");
      await refresh();
    } catch (error) { status(error.message, true); } finally { setBusy(false); }
  }
  document.addEventListener("DOMContentLoaded", function () {
    $("refreshButton").onclick = refresh; $("previousButton").onclick = function () { navigate(1); }; $("nextButton").onclick = function () { navigate(-1); }; $("followView").onchange = controls; $("customZoom").onchange = controls;
    $("targetFont").oninput = function () { state.selectedTarget = null; renderFontOptions(this.value); controls(); };
    $("targetFont").onfocus = function () { renderFontOptions(this.value); };
    $("targetFont").onkeydown = function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { if ($("fontDropdown").hidden) renderFontOptions(this.value); moveFontOption(event.key === "ArrowDown" ? 1 : -1); event.preventDefault(); }
      else if (event.key === "Enter" && state.optionIndex >= 0) { var options = $("fontDropdown").querySelectorAll(".font-option"); if (options[state.optionIndex]) options[state.optionIndex].click(); event.preventDefault(); }
      else if (event.key === "Escape") closeFontDropdown();
    };
    $("fontDropdownButton").onclick = function () { if ($("fontDropdown").hidden) { $("targetFont").focus(); renderFontOptions(""); } else closeFontDropdown(); };
    document.addEventListener("click", function (event) { if (!$("fontCombobox").contains(event.target)) closeFontDropdown(); });
    $("replaceButton").onclick = replaceFont; $("scaleTextButton").onclick = scaleText; loadFonts();
  });
}());
