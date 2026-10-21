(function(){function e(e){return e||``}onmessage=function(t){function n(t){return t.map(t=>typeof t==`number`?t:a+e(t)+a).join(i.columnDelimiter)}function r(e){return e.map(e=>typeof e==`number`||e?e:`NaN`).join(i.columnDelimiter)}let i=t.data.opts,a=i.quoteStrings?i.stringDelimiter:``,o=[n(t.data.fieldNames)].concat(t.data.frames.flat().map(e=>r(e))).join(`
`),s=Object.entries(t.data.sysConfig).map(([e,t])=>n([e,t])).join(`
`)+`
`+o;postMessage(s)}})();