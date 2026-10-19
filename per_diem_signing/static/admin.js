"use strict";
let key = "";
const status = document.querySelector("#status");
function report(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...options.headers, Authorization: `Bearer ${key}` },
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  }
  return response;
}
async function refresh() {
  const data = await (await api("/api/admin/requests")).json();
  document.querySelector("#network").textContent = data.local_only
    ? "LOCAL TEST ONLY: these links do not work on another phone. Configure an approved HTTPS public URL before sending them."
    : `Signing address: ${data.public_url}. Verify a link on cellular before sending requests.`;
  const container = document.querySelector("#requests");
  container.replaceChildren();
  for (const item of data.requests) {
    const article = document.createElement("article");
    const title = document.createElement("strong");
    title.textContent = `${item.details.payee} - Job ${item.details.job_number}`;
    article.append(title);
    const text = document.createElement("p");
    text.textContent = item.signed_at
      ? `Signed ${item.signed_at} by entered name: ${item.signer_name}`
      : item.revoked ? "Revoked"
      : `Pending; expires ${item.expires_at}`;
    article.append(text);
    const button = document.createElement("button");
    button.textContent = item.signed_at ? "Download signed Excel" : "Revoke link";
    button.disabled = item.revoked;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        if (item.signed_at) {
          const response = await api(`/api/admin/requests/${item.id}/download`);
          const url = URL.createObjectURL(await response.blob());
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = item.filename;
          anchor.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        } else {
          await api(`/api/admin/requests/${item.id}/revoke`, { method: "POST" });
          await refresh();
        }
      } catch (error) { report(error.message, true); }
      finally { button.disabled = false; }
    });
    article.append(button);
    container.append(article);
  }
}
document.querySelector("#login").addEventListener("submit", async (event) => {
  event.preventDefault();
  key = document.querySelector("#key").value.trim();
  try {
    await refresh();
    document.querySelector("#workspace").hidden = false;
    report("Ready.");
  } catch (error) {
    document.querySelector("#workspace").hidden = true;
    report(error.message, true);
  }
});
document.querySelector("#create").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    const values = new FormData(event.target);
    const details = Object.fromEntries(values);
    details.hotel = values.has("hotel");
    details.meals = values.has("meals");
    const data = await (await api("/api/admin/requests", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(details),
    })).json();
    document.querySelector("#created").hidden = false;
    document.querySelector("#link").value = data.link;
    document.querySelector("#open").href = data.link;
    await refresh();
    report("Request created. Copy its private signing link above.");
  } catch (error) { report(error.message, true); }
  finally { button.disabled = false; }
});
document.querySelector("#refresh").addEventListener("click", () => {
  refresh().catch((error) => report(error.message, true));
});
document.querySelector("#copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(document.querySelector("#link").value);
    report("Link copied.");
  } catch {
    document.querySelector("#link").select();
    report("Clipboard access unavailable. Select and copy the link manually.", true);
  }
});
