"use strict";
const token = location.pathname.split("/").pop();
const endpoint = `/api/sign/${encodeURIComponent(token)}`;
const status = document.querySelector("#status");
const canvas = document.querySelector("#signature");
const ctx = canvas.getContext("2d");
const photos = [];
let drawing = false;
let drawn = false;
let busy = false;
function report(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}
function clear() {
  ctx.fillStyle = "white";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#172b40";
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  drawn = false;
}
function point(event) {
  const bounds = canvas.getBoundingClientRect();
  return [(event.clientX - bounds.left) * canvas.width / bounds.width,
    (event.clientY - bounds.top) * canvas.height / bounds.height];
}
canvas.addEventListener("pointerdown", (event) => {
  if (busy) return;
  event.preventDefault();
  canvas.setPointerCapture(event.pointerId);
  drawing = true;
  ctx.beginPath();
  ctx.moveTo(...point(event));
});
canvas.addEventListener("pointermove", (event) => {
  if (!drawing) return;
  ctx.lineTo(...point(event));
  ctx.stroke();
  drawn = true;
});
for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
  canvas.addEventListener(type, () => { drawing = false; });
}
document.querySelector("#clear").addEventListener("click", () => { if (!busy) clear(); });
function renderPhotos() {
  const container = document.querySelector("#photo-list");
  container.replaceChildren();
  photos.forEach((file, index) => {
    const figure = document.createElement("figure");
    const image = document.createElement("img");
    const url = URL.createObjectURL(file);
    image.src = url;
    image.alt = `Receipt ${index + 1}`;
    image.onload = image.onerror = () => URL.revokeObjectURL(url);
    const caption = document.createElement("figcaption");
    caption.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.disabled = busy;
    remove.addEventListener("click", () => {
      photos.splice(index, 1);
      renderPhotos();
    });
    figure.append(image, caption, remove);
    container.append(figure);
  });
}
for (const id of ["photos", "camera"]) {
  document.getElementById(id).addEventListener("change", (event) => {
    if (busy) return;
    const additions = Array.from(event.target.files);
    const combined = [...photos, ...additions];
    if (combined.length > 10 || combined.some((file) => file.size > 10 * 1024 * 1024)
        || combined.reduce((size, file) => size + file.size, 0) > 49 * 1024 * 1024) {
      report("Use at most 10 photos, 10 MB each and 49 MB combined (leaving room for the signature).", true);
    } else if (additions.some((file) => !/\.(jpe?g|png|webp)$/i.test(file.name))) {
      report("Use JPG, PNG or WebP photos. Convert HEIC files to JPG first.", true);
    } else {
      photos.push(...additions);
      renderPhotos();
      report(`${photos.length} receipt photo(s) attached.`);
    }
    event.target.value = "";
  });
}
document.querySelector("#sign").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  if (!drawn) { report("Draw your signature first.", true); return; }
  busy = true;
  const controls = Array.from(event.target.querySelectorAll("input,button"));
  controls.forEach((element) => { element.disabled = true; });
  renderPhotos();
  report("Uploading and saving. Please keep this page open...");
  try {
    const body = new FormData();
    body.append("name", document.querySelector("#name").value.trim());
    body.append("consent", document.querySelector("#consent").checked ? "yes" : "no");
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("Could not capture the signature. Please try again.");
    body.append("signature", blob, "signature.png");
    photos.forEach((photo) => body.append("photos", photo, photo.name));
    const response = await fetch(`${endpoint}/submit`, { method: "POST", body });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Submission failed.");
    document.querySelector("#content").hidden = true;
    report("Submitted successfully. Your signature and photos have been saved in the Excel workbook. You may close this page.");
  } catch (error) {
    report(`${error.message} If your connection dropped, check with the sender before retrying; the submission may already have saved.`, true);
  } finally {
    busy = false;
    controls.forEach((element) => { element.disabled = false; });
    renderPhotos();
  }
});
clear();
fetch(endpoint).then(async (response) => {
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail);
  document.querySelector("#heading").textContent = `${data.details.payee} - Job ${data.details.job_number}`;
  document.querySelector("#consent-text").textContent = data.consent;
  document.querySelector("#preview").src = `${endpoint}/preview`;
  document.querySelector("#content").hidden = false;
  report("Review the form, attach receipts, and sign below.");
}).catch((error) => report(error.message, true));
