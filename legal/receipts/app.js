const $ = id => document.getElementById(id);
let api, manifest, proof, verifiedPurpose;
const fragment = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
const match = /^([a-f0-9]{32})\.([A-Za-z0-9_-]{20,128})$/.exec(fragment);
if (match) proof = {challenge: match[1], token: match[2]};
function status(message) { $('status').textContent = message; }
async function request(path, body) {
  const response = await fetch(api + path, {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body), credentials:'omit', cache:'no-store', referrerPolicy:'no-referrer'});
  const value = await response.json();
  if (!response.ok) throw Error(value.code || 'request_failed');
  return value;
}
function download(value) {
  const blob = new Blob([JSON.stringify(value) + '\n'], {type:'application/json'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = 'acceptance-receipt.json';
  link.textContent = 'Download your signed receipt'; $('downloads').replaceChildren(link); $('results').hidden = false;
}
function documentLink() {
  const doc = manifest.documents.find(d => d.id === $('agreement').value);
  $('document').replaceChildren(); if (!doc) return;
  const a = document.createElement('a'); a.href = doc.url; a.textContent = `${doc.title} · ${doc.version}`;
  const p = document.createElement('p'); p.textContent = `SHA-256: ${doc.sha256}`;
  $('document').append(a, p);
}
function purpose() {
  $('agreement-label').hidden = $('purpose').value !== 'accept';
  $('document').hidden = $('purpose').value !== 'accept';
}
$('agreement').onchange = documentLink; $('purpose').onchange = purpose;
$('request').onsubmit = async event => {
  event.preventDefault(); $('request').querySelector('button').disabled = true;
  try {
    const body = {email:$('email').value, purpose:$('purpose').value};
    if (body.purpose === 'accept') body.document_id = $('agreement').value;
    await request('/challenges', body);
    status('Check your email. The link expires in 15 minutes. It will not accept an agreement by itself.');
  } catch (error) { status(error.message); }
  finally { $('request').querySelector('button').disabled = false; }
};
$('finish').onclick = async () => {
  if (!proof) return;
  $('finish').disabled = true;
  try {
    // Resolve purpose and bound document using the capability; never take them from browser storage.
    const info = await request('/verification', proof);
    verifiedPurpose = info.purpose;
    if (info.document) {
      const selected = manifest.documents.find(d => d.id === info.document.id && d.sha256 === info.document.sha256);
      if (!selected) throw Error('agreement_changed');
      $('agreement').value = selected.id; documentLink(); $('document').hidden = false;
    }
    if (verifiedPurpose === 'accept') {
      if (!$('confirm').checked) throw Error('explicit_acceptance_required');
      const upload = await request('/uploads', {...proof, confirm:true});
      const target = new URL(upload.upload_url);
      if (target.protocol !== 'https:' || !/^[a-z0-9.-]+\.s3(?:\.us-east-1)?\.amazonaws\.com$/.test(target.hostname)) throw Error('invalid_upload_target');
      const sent = await fetch(upload.upload_url, {method:'PUT', headers:upload.headers, body:upload.payload,
        credentials:'omit', referrerPolicy:'no-referrer'});
      if (!sent.ok && sent.status !== 412) throw Error('upload_failed');
      download(await request('/finalize', proof)); status('Acceptance recorded. Keep your signed receipt.');
    } else if (verifiedPurpose === 'close') {
      if (!$('confirm').checked) throw Error('explicit_closure_required');
      const result = await request('/close', {...proof, confirm:true});
      status(`Closure recorded. Receipt retention ends ${result.receipt_retention_until}.`);
    } else {
      const result = await request('/receipts', proof); $('downloads').replaceChildren();
      for (const receipt of result.receipts) {
        const a = document.createElement('a'); const target = new URL(receipt.download_url);
        if (target.protocol !== 'https:' || !/^[a-z0-9.-]+\.s3(?:\.us-east-1)?\.amazonaws\.com$/.test(target.hostname)) throw Error('invalid_download_target');
        a.href = receipt.download_url; a.textContent = `Download receipt ${receipt.receipt_id}`;
        a.referrerPolicy = 'no-referrer'; $('downloads').append(a, document.createElement('br'));
      }
      $('results').hidden = false; status(result.receipts.length ? 'Private download links expire in two minutes.' : 'No retained receipts found.');
    }
    proof = null; $('verified').hidden = true;
  } catch (error) { status(error.message); }
  finally { $('finish').disabled = false; }
};
async function start() {
  try {
    const [configResponse, manifestResponse] = await Promise.all([fetch('/legal/receipt-config.json'), fetch('/legal/manifest.json')]);
    const config = await configResponse.json(); manifest = await manifestResponse.json();
    if (!config.api) { status('The receipt service is not activated. Agreements remain available for reading.'); return; }
    const endpoint = new URL(config.api);
    if (endpoint.protocol !== 'https:' || !/^[a-z0-9]+\.execute-api\.us-east-1\.amazonaws\.com$/.test(endpoint.hostname) || endpoint.pathname !== '/') throw Error('invalid_service_configuration');
    api = config.api.replace(/\/$/, '');
    for (const doc of manifest.documents) {
      const option = document.createElement('option'); option.value = doc.id; option.textContent = doc.title; $('agreement').append(option);
    }
    documentLink(); purpose(); $('request').hidden = Boolean(proof); $('verified').hidden = !proof;
    if (proof) {
      const info = await request('/verification', proof); verifiedPurpose = info.purpose;
      if (info.document) { $('agreement').value = info.document.id; documentLink(); }
      $('document').hidden = !info.document; $('verified').prepend($('document'));
      $('confirm').parentElement.hidden = info.purpose === 'access';
      status(`Verified link for ${info.purpose}. Review the details before continuing.`);
    } else { status('Choose a request. No acceptance is recorded until you verify your email and confirm.'); }
  } catch (error) { status(error.message); }
}
start();
