(function(){onmessage=function(e){let t=e.data.fieldNames.indexOf(`time`),n=e.data.fieldNames.indexOf(`GPS_coord[0]`),r=e.data.fieldNames.indexOf(`GPS_coord[1]`),i=e.data.fieldNames.indexOf(`GPS_altitude`),a=``;for(let o of e.data.frames)for(let s of o){if(!s[n]||!s[r])continue;let o=Math.floor(s[t]/1e3),c=s[n]/1e7,l=s[r]/1e7,u=s[i]/10,d=new Date(e.data.sysConfig[`Log start datetime`]);d.setTime(d.getTime()+o);let f=`<trkpt lat="${c}" lon="${l}">`;f+=`<ele>${u}</ele>`,f+=`<time>${d.toISOString()}</time>`,f+=`</trkpt>
`,a+=f}let o=`  <trk>
    <trkseg>
      ${a}
    </trkseg>
  </trk>`;postMessage(`<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://www.topografix.com/GPX/1/1" xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd http://www.topografix.com/GPX/gpx_style/0/2 http://www.topografix.com/GPX/gpx_style/0/2/gpx_style.xsd" xmlns:gpx_style="http://www.topografix.com/GPX/gpx_style/0/2" 
  version="1.1" 
  creator="https://github.com/betaflight/betaflight-configurator">
  <metadata>
    <author>
      <name>Betaflight App</name>
      <link href="https://github.com/betaflight/betaflight-configurator"></link>
    </author>
  </metadata>
`+o+`
</gpx>`)}})();