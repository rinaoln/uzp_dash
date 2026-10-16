"""Выгрузка в .xlsx прямо из браузера — без единой внешней библиотеки.

Отчёты уезжают по почте и открываются в закрытом контуре, где нет ни сети, ни
CDN: файл обязан уметь собрать книгу Excel сам. Здесь лежит ровно эта часть —
zip без сжатия, минимальный пакет OOXML и точка входа `xlsxBook`.

Отличие от выгрузки внутри дэша tb_health (там свой экземпляр того же приёма на
ОДИН лист): здесь книга МНОГОЛИСТОВАЯ. Слайд и таблица — разные сущности, и
складывать «Ведомства» и «Организации» в один лист значило бы склеить две
таблицы с разными колонками.

Числа уходят числами: «12 345» и «−1 234» в Excel должны суммироваться и
сортироваться. Значение со знаком процента («−4,9%») остаётся текстом: это
подпись, а не доля, и превращать её в −0.049 значило бы поменять смысл колонки;
нужны проценты числом — выгружайте их без знака, вынеся «%» в заголовок.

Код с ведущим нулём тоже остаётся текстом. ИНН организации из региона с номером
меньше десяти начинается с нуля, и в числовой ячейке Excel этот ноль теряется
молча — вместе с возможностью найти организацию по ИНН.
"""
from __future__ import annotations

XLSX_JS = r"""
<script>
(function(){
  var CRC = (function(){
    var t = new Uint32Array(256), c, n, k;
    for(n = 0; n < 256; n++){
      c = n;
      for(k = 0; k < 8; k++){ c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1); }
      t[n] = c >>> 0;
    }
    return t;
  })();
  function crc32(buf){
    var c = 0xFFFFFFFF;
    for(var i = 0; i < buf.length; i++){ c = CRC[(c ^ buf[i]) & 0xFF] ^ (c >>> 8); }
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  var enc = new TextEncoder();

  /* zip без сжатия: локальный заголовок на файл, центральный каталог, хвост */
  function zip(files){
    var parts = [], central = [], offset = 0, cdSize = 0;
    files.forEach(function(f){
      var name = enc.encode(f.name), data = f.data;
      var crc = crc32(data), size = data.length;
      var lh = new Uint8Array(30 + name.length), dv = new DataView(lh.buffer);
      dv.setUint32(0, 0x04034b50, true);
      dv.setUint16(4, 20, true);
      dv.setUint16(6, 0x0800, true);          /* имена файлов в UTF-8 */
      dv.setUint32(14, crc, true);
      dv.setUint32(18, size, true);
      dv.setUint32(22, size, true);
      dv.setUint16(26, name.length, true);
      lh.set(name, 30);
      parts.push(lh, data);

      var ch = new Uint8Array(46 + name.length), cv = new DataView(ch.buffer);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);
      cv.setUint16(8, 0x0800, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, size, true);
      cv.setUint32(24, size, true);
      cv.setUint16(28, name.length, true);
      cv.setUint32(42, offset, true);
      ch.set(name, 46);
      central.push(ch);
      cdSize += ch.length;
      offset += lh.length + size;
    });
    var end = new Uint8Array(22), ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(8, files.length, true);
    ev.setUint16(10, files.length, true);
    ev.setUint32(12, cdSize, true);
    ev.setUint32(16, offset, true);
    return new Blob(parts.concat(central, [end]),
      {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
  }

  function xesc(s){
    return String(s == null ? '' : s)
      .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function colName(i){
    var s = '';
    do { s = String.fromCharCode(65 + (i % 26)) + s; i = Math.floor(i / 26) - 1; }
    while(i >= 0);
    return s;
  }
  /* «12 345» и «−1 234» — числа; «−4,9%», код с ведущим нулём
     (ИНН региона 01–09) и всё остальное — текст */
  function cellVal(v){
    if(typeof v === 'number') return isFinite(v) ? v : '';
    var t = String(v == null ? '' : v)
      .replace(/−/g, '-').replace(/[\s  ]/g, '');
    if(/^0\d/.test(t)) return String(v);          /* код, а не число */
    if(/^-?\d+(?:[.,]\d+)?$/.test(t)) return Number(t.replace(',', '.'));
    return String(v == null ? '' : v);
  }
  function sheetXml(rows){
    var out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
      + '<sheetData>'];
    (rows || []).forEach(function(row, ri){
      out.push('<row r="' + (ri + 1) + '">');
      (row || []).forEach(function(v, ci){
        var ref = colName(ci) + (ri + 1), val = cellVal(v);
        if(typeof val === 'number'){
          out.push('<c r="' + ref + '"><v>' + val + '</v></c>');
        } else if(val !== ''){
          out.push('<c r="' + ref + '" t="inlineStr"><is><t xml:space="preserve">'
            + xesc(val) + '</t></is></c>');
        }
      });
      out.push('</row>');
    });
    out.push('</sheetData></worksheet>');
    return out.join('');
  }
  function sheetName(title, i){
    var s = String(title || ('Лист' + i)).replace(/[\[\]:*?\/\\]/g, ' ').trim();
    return s.slice(0, 28) || ('Лист' + i);
  }
  function fileName(name){
    var s = String(name || 'Выгрузка').replace(/[\\/:*?"<>|]/g, '-')
      .replace(/\s+/g, ' ').trim();
    return (s.slice(0, 90) || 'Выгрузка') + '.xlsx';
  }

  /* Публичная точка: книга из нескольких листов.
     sheets = [{name: 'Ведомства', rows: [[...], ...]}, ...] */
  window.xlsxBook = function(name, sheets){
    sheets = (sheets || []).filter(function(s){ return s && s.rows && s.rows.length; });
    if(!sheets.length) return;
    var types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      + '<Default Extension="xml" ContentType="application/xml"/>'
      + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'];
    var book = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
      + 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'];
    var rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'];
    var files = [];
    sheets.forEach(function(s, i){
      var n = i + 1;
      types.push('<Override PartName="/xl/worksheets/sheet' + n + '.xml" '
        + 'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>');
      book.push('<sheet name="' + xesc(sheetName(s.name, n)) + '" sheetId="' + n
        + '" r:id="rId' + n + '"/>');
      rels.push('<Relationship Id="rId' + n + '" Type="http://schemas.openxmlformats.org'
        + '/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet'
        + n + '.xml"/>');
      files.push({name: 'xl/worksheets/sheet' + n + '.xml',
                  data: enc.encode(sheetXml(s.rows))});
    });
    types.push('</Types>');
    book.push('</sheets></workbook>');
    rels.push('</Relationships>');
    files = [
      {name: '[Content_Types].xml', data: enc.encode(types.join(''))},
      {name: '_rels/.rels', data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        + '</Relationships>')},
      {name: 'xl/workbook.xml', data: enc.encode(book.join(''))},
      {name: 'xl/_rels/workbook.xml.rels', data: enc.encode(rels.join(''))}
    ].concat(files);
    var url = URL.createObjectURL(zip(files));
    var a = document.createElement('a');
    a.href = url;
    a.download = fileName(name);
    document.body.appendChild(a);
    a.click();
    setTimeout(function(){ a.remove(); URL.revokeObjectURL(url); }, 0);
  };
})();
</script>
"""
