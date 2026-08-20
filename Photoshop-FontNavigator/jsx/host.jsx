#target photoshop

var FontNavigator = (function () {
    var s2t = stringIDToTypeID;
    var c2t = charIDToTypeID;
    var pendingSource = "";
    var pendingTarget = null;
    var pendingChanged = 0;
    var pendingScale = null;
    var pendingScaleSkipped = 0;
    var installedFontCache = null;

    function escapeString(value) {
        return '"' + String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\r/g, "\\r").replace(/\n/g, "\\n").replace(/\t/g, "\\t") + '"';
    }

    function stringify(value) {
        if (value === null) return "null";
        if (typeof value === "string") return escapeString(value);
        if (typeof value === "number" || typeof value === "boolean") return String(value);
        if (value instanceof Array) {
            var items = [];
            for (var i = 0; i < value.length; i++) items.push(stringify(value[i]));
            return "[" + items.join(",") + "]";
        }
        var pairs = [];
        for (var key in value) if (value.hasOwnProperty(key)) pairs.push(escapeString(key) + ":" + stringify(value[key]));
        return "{" + pairs.join(",") + "}";
    }

    function success(data) { return stringify({ ok: true, data: data }); }
    function failure(error) { return stringify({ ok: false, error: error && error.message ? error.message : String(error) }); }

    function getTextDescriptor(layerId) {
        var reference = new ActionReference();
        reference.putProperty(c2t("Prpr"), s2t("textKey"));
        reference.putIdentifier(c2t("Lyr "), layerId);
        var result = executeActionGet(reference);
        return result.getObjectValue(s2t("textKey"));
    }

    function styleInfo(style) {
        var ps = style.hasKey(s2t("fontPostScriptName")) ? style.getString(s2t("fontPostScriptName")) : "";
        var family = style.hasKey(s2t("fontName")) ? style.getString(s2t("fontName")) : ps;
        var fontStyle = style.hasKey(s2t("fontStyleName")) ? style.getString(s2t("fontStyleName")) : "";
        return ps || family ? { postScriptName: ps || family, family: family || ps, style: fontStyle } : null;
    }

    function fontsInDescriptor(text) {
        var result = {}, key, i, range, style, info;
        if (text.hasKey(s2t("textStyle"))) {
            info = styleInfo(text.getObjectValue(s2t("textStyle")));
            if (info) result[info.postScriptName] = info;
        }
        if (text.hasKey(s2t("textStyleRange"))) {
            var ranges = text.getList(s2t("textStyleRange"));
            for (i = 0; i < ranges.count; i++) {
                range = ranges.getObjectValue(i);
                if (!range.hasKey(s2t("textStyle"))) continue;
                style = range.getObjectValue(s2t("textStyle"));
                info = styleInfo(style);
                if (info) result[info.postScriptName] = info;
            }
        }
        return result;
    }

    function readableFontName(family, style, fallback) {
        var readableFamily = String(family || fallback || "");
        var readableStyle = String(style || "");
        if (!readableStyle) return readableFamily;
        if (readableFamily.toLowerCase().indexOf(readableStyle.toLowerCase()) !== -1) return readableFamily;
        return readableFamily + " " + readableStyle;
    }

    function installedFontMap() {
        if (installedFontCache) return installedFontCache;
        var result = {};
        for (var i = 0; i < app.fonts.length; i++) {
            var font = app.fonts[i];
            result[font.postScriptName] = {
                displayName: readableFontName(font.family, font.style, font.name || font.postScriptName),
                family: font.family,
                style: font.style,
                postScriptName: font.postScriptName
            };
        }
        installedFontCache = result;
        return installedFontCache;
    }

    function documentLayerCount() {
        var reference = new ActionReference();
        reference.putProperty(c2t("Prpr"), s2t("numberOfLayers"));
        reference.putEnumerated(c2t("Dcmn"), c2t("Ordn"), c2t("Trgt"));
        return executeActionGet(reference).getInteger(s2t("numberOfLayers"));
    }

    function collectTextLayerDescriptors(output) {
        var count = documentLayerCount();
        for (var index = 1; index <= count; index++) {
            var reference = new ActionReference();
            reference.putIndex(c2t("Lyr "), index);
            var descriptor = executeActionGet(reference);
            if (!descriptor.hasKey(s2t("textKey"))) continue;
            output.push({
                id: descriptor.getInteger(s2t("layerID")),
                name: descriptor.hasKey(s2t("name")) ? descriptor.getString(s2t("name")) : "",
                text: descriptor.getObjectValue(s2t("textKey"))
            });
        }
    }

    function collectTextLayers(container, output) {
        for (var i = 0; i < container.layers.length; i++) {
            var layer = container.layers[i];
            if (layer.typename === "LayerSet") collectTextLayers(layer, output);
            else if (layer.typename === "ArtLayer" && layer.kind === LayerKind.TEXT) output.push({ id: layer.id, name: layer.name });
        }
    }

    function scanDocument() {
        try {
            if (!app.documents.length) throw new Error("当前没有打开的 Photoshop 文档。");
            var layers = [], map = {}, order = [], installed = installedFontMap();
            collectTextLayerDescriptors(layers);
            for (var i = 0; i < layers.length; i++) {
                var layer = layers[i], found = fontsInDescriptor(layer.text);
                for (var ps in found) if (found.hasOwnProperty(ps)) {
                    if (!map[ps]) {
                        var known = installed[ps];
                        map[ps] = {
                            postScriptName: ps,
                            displayName: known ? known.displayName : readableFontName(found[ps].family, found[ps].style, ps),
                            family: known ? known.family : found[ps].family,
                            style: known ? known.style : found[ps].style,
                            missing: !known,
                            layers: []
                        };
                        order.push(ps);
                    }
                    map[ps].layers.push({ id: layer.id, name: layer.name });
                }
            }
            var fonts = [];
            for (i = 0; i < order.length; i++) fonts.push(map[order[i]]);
            return success({ fonts: fonts });
        } catch (error) { return failure(error); }
    }

    function getInstalledFonts() {
        try {
            var fonts = [];
            for (var i = 0; i < app.fonts.length; i++) {
                var font = app.fonts[i];
                fonts.push({ postScriptName: font.postScriptName, displayName: readableFontName(font.family, font.style, font.name || font.postScriptName), family: font.family, style: font.style });
            }
            function nameGroup(name) {
                var first = String(name || "").charAt(0);
                if (/[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/.test(first)) return 0;
                if (/[A-Za-z]/.test(first)) return 1;
                return 2;
            }
            fonts.sort(function (a, b) {
                var aName = a.displayName || a.family || a.postScriptName;
                var bName = b.displayName || b.family || b.postScriptName;
                var groupDifference = nameGroup(aName) - nameGroup(bName);
                if (groupDifference) return groupDifference;
                aName = aName.toLowerCase(); bName = bName.toLowerCase();
                if (aName === bName) return 0;
                return aName < bName ? -1 : 1;
            });
            return success(fonts);
        } catch (error) { return failure(error); }
    }

    function setViewZoom(percent) {
        var reference = new ActionReference();
        reference.putProperty(s2t("property"), s2t("zoom"));
        reference.putEnumerated(s2t("document"), s2t("ordinal"), s2t("targetEnum"));
        var descriptor = new ActionDescriptor();
        descriptor.putReference(s2t("null"), reference);
        var zoomDescriptor = new ActionDescriptor();
        zoomDescriptor.putDouble(s2t("zoom"), Number(percent) / 100);
        descriptor.putObject(s2t("to"), s2t("zoom"), zoomDescriptor);
        executeAction(s2t("set"), descriptor, DialogModes.NO);
    }

    function selectLayer(layerId, followView, zoomPercent) {
        try {
            var reference = new ActionReference(); reference.putIdentifier(c2t("Lyr "), Number(layerId));
            var descriptor = new ActionDescriptor(); descriptor.putReference(c2t("null"), reference); descriptor.putBoolean(c2t("MkVs"), false);
            executeAction(c2t("slct"), descriptor, DialogModes.NO);
            var viewFollowed = false, viewError = "", zoomApplied = false;
            if (followView) {
                try {
                    app.runMenuItem(s2t("fitLayersOnScreen"));
                    viewFollowed = true;
                    if (Number(zoomPercent) > 0) {
                        try { setViewZoom(Number(zoomPercent)); zoomApplied = true; }
                        catch (zoomError) { viewError = zoomError && zoomError.message ? zoomError.message : String(zoomError); }
                    }
                } catch (followError) {
                    viewError = followError && followError.message ? followError.message : String(followError);
                }
            }
            return success({ selected: true, viewFollowed: viewFollowed, zoomApplied: zoomApplied, viewError: viewError });
        } catch (error) { return failure(error); }
    }

    function targetFontInfo(postScriptName) {
        for (var i = 0; i < app.fonts.length; i++) if (app.fonts[i].postScriptName === postScriptName) {
            return { postScriptName: app.fonts[i].postScriptName, family: app.fonts[i].family, style: app.fonts[i].style };
        }
        throw new Error("找不到目标字体：" + postScriptName);
    }

    function replaceStyle(style) {
        var current = styleInfo(style);
        if (!current || current.postScriptName !== pendingSource) return false;
        style.putString(s2t("fontPostScriptName"), pendingTarget.postScriptName);
        style.putString(s2t("fontName"), pendingTarget.family);
        style.putString(s2t("fontStyleName"), pendingTarget.style);
        return true;
    }

    function replaceLayer(layerId) {
        var text = getTextDescriptor(layerId), changed = false, i;
        if (text.hasKey(s2t("textStyle"))) {
            var baseStyle = text.getObjectValue(s2t("textStyle"));
            if (replaceStyle(baseStyle)) { text.putObject(s2t("textStyle"), s2t("textStyle"), baseStyle); changed = true; }
        }
        if (text.hasKey(s2t("textStyleRange"))) {
            var oldRanges = text.getList(s2t("textStyleRange")), newRanges = new ActionList();
            for (i = 0; i < oldRanges.count; i++) {
                var range = oldRanges.getObjectValue(i);
                if (range.hasKey(s2t("textStyle"))) {
                    var style = range.getObjectValue(s2t("textStyle"));
                    if (replaceStyle(style)) { range.putObject(s2t("textStyle"), s2t("textStyle"), style); changed = true; }
                }
                newRanges.putObject(s2t("textStyleRange"), range);
            }
            if (changed) text.putList(s2t("textStyleRange"), newRanges);
        }
        if (!changed) return false;
        var reference = new ActionReference(); reference.putIdentifier(c2t("Lyr "), layerId);
        var setDescriptor = new ActionDescriptor(); setDescriptor.putReference(c2t("null"), reference); setDescriptor.putObject(c2t("T   "), s2t("textLayer"), text);
        executeAction(c2t("setd"), setDescriptor, DialogModes.NO);
        return true;
    }

    function replacePending() {
        var layers = []; collectTextLayers(app.activeDocument, layers); pendingChanged = 0;
        for (var i = 0; i < layers.length; i++) if (replaceLayer(layers[i].id)) pendingChanged++;
    }

    function replaceFont(source, target) {
        try {
            if (!app.documents.length) throw new Error("当前没有打开的 Photoshop 文档。");
            pendingSource = String(source); pendingTarget = targetFontInfo(String(target)); pendingChanged = 0;
            app.activeDocument.suspendHistory("批量替换字体", "FontNavigator._replacePending()");
            return success({ changedLayers: pendingChanged });
        } catch (error) { return failure(error); }
    }

    function numericValue(descriptor, key) {
        var type = descriptor.getType(key);
        if (type === DescValueType.UNITDOUBLE) return descriptor.getUnitDoubleValue(key);
        if (type === DescValueType.DOUBLETYPE) return descriptor.getDouble(key);
        if (type === DescValueType.INTEGERTYPE) return descriptor.getInteger(key);
        return null;
    }

    function scaleNumeric(descriptor, key, factor) {
        if (!descriptor.hasKey(key)) { pendingScaleSkipped++; return false; }
        var type = descriptor.getType(key), value = numericValue(descriptor, key);
        if (value === null) { pendingScaleSkipped++; return false; }
        value *= factor;
        if (type === DescValueType.UNITDOUBLE) descriptor.putUnitDouble(key, descriptor.getUnitDoubleType(key), value);
        else if (type === DescValueType.DOUBLETYPE) descriptor.putDouble(key, value);
        else descriptor.putInteger(key, Math.round(value));
        return true;
    }

    function scaleTextStyle(style) {
        var changed = false;
        var sizeKey = s2t("size"), leadingKey = s2t("leading"), autoKey = s2t("autoLeading"), trackingKey = s2t("tracking");
        var originalSize = style.hasKey(sizeKey) ? numericValue(style, sizeKey) : null;
        var automatic = style.hasKey(autoKey) && style.getBoolean(autoKey);
        if (pendingScale.leading !== 1) {
            if (automatic) {
                var originalLeading = style.hasKey(leadingKey) ? numericValue(style, leadingKey) : (originalSize === null ? null : originalSize * 1.2);
                if (originalLeading === null) pendingScaleSkipped++;
                else {
                    style.putBoolean(autoKey, false);
                    style.putUnitDouble(leadingKey, s2t("pointsUnit"), originalLeading * pendingScale.leading);
                    changed = true;
                }
            } else if (scaleNumeric(style, leadingKey, pendingScale.leading)) changed = true;
        }
        if (pendingScale.size !== 1 && scaleNumeric(style, sizeKey, pendingScale.size)) changed = true;
        if (pendingScale.tracking !== 1 && scaleNumeric(style, trackingKey, pendingScale.tracking)) changed = true;
        return changed;
    }

    function scaleLayer(layerId) {
        var text = getTextDescriptor(layerId), changed = false, i;
        if (text.hasKey(s2t("textStyle"))) {
            var baseStyle = text.getObjectValue(s2t("textStyle"));
            if (scaleTextStyle(baseStyle)) { text.putObject(s2t("textStyle"), s2t("textStyle"), baseStyle); changed = true; }
        }
        if (text.hasKey(s2t("textStyleRange"))) {
            var oldRanges = text.getList(s2t("textStyleRange")), newRanges = new ActionList(), rangesChanged = false;
            for (i = 0; i < oldRanges.count; i++) {
                var range = oldRanges.getObjectValue(i);
                if (range.hasKey(s2t("textStyle"))) {
                    var style = range.getObjectValue(s2t("textStyle"));
                    if (scaleTextStyle(style)) { range.putObject(s2t("textStyle"), s2t("textStyle"), style); rangesChanged = true; }
                }
                newRanges.putObject(s2t("textStyleRange"), range);
            }
            if (rangesChanged) { text.putList(s2t("textStyleRange"), newRanges); changed = true; }
        }
        if (!changed) return false;
        var reference = new ActionReference(); reference.putIdentifier(c2t("Lyr "), layerId);
        var setDescriptor = new ActionDescriptor(); setDescriptor.putReference(c2t("null"), reference); setDescriptor.putObject(c2t("T   "), s2t("textLayer"), text);
        executeAction(c2t("setd"), setDescriptor, DialogModes.NO);
        return true;
    }

    function scalePending() {
        var layers = []; collectTextLayers(app.activeDocument, layers); pendingChanged = 0; pendingScaleSkipped = 0;
        for (var i = 0; i < layers.length; i++) if (scaleLayer(layers[i].id)) pendingChanged++;
    }

    function scaleTextProperties(sizeFactor, leadingFactor, trackingFactor) {
        try {
            if (!app.documents.length) throw new Error("当前没有打开的 Photoshop 文档。");
            pendingScale = { size: Number(sizeFactor), leading: Number(leadingFactor), tracking: Number(trackingFactor) };
            if (!(pendingScale.size > 0 && pendingScale.size <= 100 && pendingScale.leading > 0 && pendingScale.leading <= 100 && pendingScale.tracking > 0 && pendingScale.tracking <= 100)) throw new Error("缩放倍数必须大于 0 且不超过 100。");
            app.activeDocument.suspendHistory("批量缩放文字属性", "FontNavigator._scalePending()");
            return success({ changedLayers: pendingChanged, skippedProperties: pendingScaleSkipped });
        } catch (error) { return failure(error); }
    }

    return { scanDocument: scanDocument, getInstalledFonts: getInstalledFonts, selectLayer: selectLayer, replaceFont: replaceFont, scaleTextProperties: scaleTextProperties, _replacePending: replacePending, _scalePending: scalePending };
}());
