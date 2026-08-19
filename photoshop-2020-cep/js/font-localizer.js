(function (root) {
  "use strict";

  function u16(buffer, offset) { return buffer.readUInt16BE(offset); }
  function u32(buffer, offset) { return buffer.readUInt32BE(offset); }

  function decodeName(buffer, offset, length, platform) {
    var end = offset + length, result = "", i, code;
    if (offset < 0 || end > buffer.length) return "";
    if (platform === 0 || platform === 3) {
      for (i = offset; i + 1 < end; i += 2) {
        code = u16(buffer, i);
        if (code) result += String.fromCharCode(code);
      }
      return result.replace(/^\s+|\s+$/g, "");
    }
    return buffer.toString("latin1", offset, end).replace(/^\s+|\s+$/g, "");
  }

  function languageRank(platform, language) {
    if (platform === 3) {
      if (language === 0x0804 || language === 0x1004) return 0;
      if (language === 0x0404 || language === 0x0c04 || language === 0x1404) return 1;
      if ((language & 0x03ff) === 0x0009) return 2;
    }
    if (platform === 0) return 3;
    if (platform === 1 && language === 0) return 2;
    return 4;
  }

  function best(records, ids) {
    var winner = null, i, j, record, rank;
    for (i = 0; i < records.length; i++) {
      record = records[i];
      for (j = 0; j < ids.length; j++) if (record.id === ids[j] && record.value) {
        rank = languageRank(record.platform, record.language);
        if (!winner || rank < winner.rank || (rank === winner.rank && j < winner.idRank)) {
          winner = { value: record.value, rank: rank, idRank: j };
        }
      }
    }
    return winner ? winner.value : "";
  }

  function parseFace(buffer, faceOffset, output, collection) {
    if (faceOffset + 12 > buffer.length) return;
    var tableCount = u16(buffer, faceOffset + 4), nameOffset = -1, i, entry, count, strings, records = [];
    for (i = 0; i < tableCount; i++) {
      entry = faceOffset + 12 + i * 16;
      if (entry + 16 > buffer.length) return;
      if (buffer.toString("ascii", entry, entry + 4) === "name") nameOffset = (collection ? 0 : faceOffset) + u32(buffer, entry + 8);
    }
    if (nameOffset < 0 || nameOffset + 6 > buffer.length) return;
    count = u16(buffer, nameOffset + 2); strings = nameOffset + u16(buffer, nameOffset + 4);
    for (i = 0; i < count; i++) {
      entry = nameOffset + 6 + i * 12;
      if (entry + 12 > buffer.length) break;
      records.push({
        platform: u16(buffer, entry), language: u16(buffer, entry + 4), id: u16(buffer, entry + 6),
        value: decodeName(buffer, strings + u16(buffer, entry + 10), u16(buffer, entry + 8), u16(buffer, entry))
      });
    }
    var postScriptName = best(records, [6]);
    if (!postScriptName) return;
    var family = best(records, [16, 1]);
    var style = best(records, [17, 2]);
    output[postScriptName] = { family: family, style: style };
  }

  function parseFile(fs, file, output) {
    try {
      var buffer = fs.readFileSync(file), count, i;
      if (buffer.toString("ascii", 0, 4) === "ttcf") {
        count = u32(buffer, 8);
        for (i = 0; i < count; i++) parseFace(buffer, u32(buffer, 12 + i * 4), output, true);
      } else parseFace(buffer, 0, output, false);
    } catch (ignore) {}
  }

  function scanDirectory(fs, path, directory, output) {
    if (!directory || !fs.existsSync(directory)) return;
    var names;
    try { names = fs.readdirSync(directory); } catch (ignore) { return; }
    for (var i = 0; i < names.length; i++) {
      var file = path.join(directory, names[i]);
      if (/\.(otf|ttf|ttc|otc)$/i.test(names[i])) parseFile(fs, file, output);
    }
  }

  function load() {
    var output = {};
    if (typeof require !== "function") return output;
    try {
      var fs = require("fs"), path = require("path");
      scanDirectory(fs, path, process.env.WINDIR ? path.join(process.env.WINDIR, "Fonts") : "", output);
      scanDirectory(fs, path, process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "Microsoft", "Windows", "Fonts") : "", output);
    } catch (ignore) {}
    return output;
  }

  root.FontNameLocalizer = { load: load };
}(this));
