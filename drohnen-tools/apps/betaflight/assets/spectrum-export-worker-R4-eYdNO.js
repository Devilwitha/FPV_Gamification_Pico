(function(){onmessage=function(e){let t=e.data.opts.columnDelimiter,n=e.data.fftOutput,r=n.length,i=.5*e.data.blackBoxRate/r,a=`x`+t+`y
`;for(let e=0;e<r;e++){let r=i*e;a+=r.toString()+t+n[e].toString()+`
`}postMessage(a)}})();