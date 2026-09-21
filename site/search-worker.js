let rows=[];
self.onmessage=({data})=>{if(data.type==='load'){rows=data.rows;postMessage({type:'ready'});return}const{q,page=1,id}=data;// Readings are hiragana, so katakana input also matches them as hiragana.
const h=q.replace(/[ァ-ヶ]/g,c=>String.fromCharCode(c.charCodeAt(0)-0x60));const exact=[],partial=[];for(const row of rows){if(row.word===q||row.reading===h)exact.push(row);else if(row.word.includes(q)||row.reading.includes(h))partial.push(row)}postMessage({type:'results',id,q,page,exact,total:partial.length,items:partial.slice((page-1)*20,page*20)});};
