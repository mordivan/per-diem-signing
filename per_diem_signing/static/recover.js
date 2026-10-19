"use strict";
const token = location.pathname.split("/").pop();
const endpoint = `/api/auth/reset/${encodeURIComponent(token)}`;
let kind = "";
const status = document.querySelector("#status");
function report(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-PerDiem-Request": "1" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function clearPairing() {
  document.querySelector("#qr").removeAttribute("src");
  document.querySelector("#secret").textContent = "";
  document.querySelector("#pairing").hidden = true;
  document.querySelector("#code").value = "";
}
function submit(id, action) {
  document.querySelector(id).addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    button.disabled = true;
    try { await action(event.target); }
    catch (error) { report(error.message, true); }
    finally { button.disabled = false; }
  });
}
submit("#start", async (form) => {
  const password = document.querySelector("#password").value;
  if (kind !== "authenticator" && password !== document.querySelector("#repeat").value) {
    throw new Error("Passwords do not match.");
  }
  const data = await api(`${endpoint}/start`, { password });
  form.reset();
  form.hidden = true;
  if (data.secret) {
    document.querySelector("#qr").src = `data:image/png;base64,${data.qr}`;
    document.querySelector("#secret").textContent = data.secret;
    document.querySelector("#pairing").hidden = false;
  }
  document.querySelector("#verify").hidden = false;
  report(kind === "password" ? "Enter a fresh code from your existing authenticator."
    : "Pair your new authenticator, then enter its six-digit code.");
});
submit("#confirm", async () => {
  const data = await api(`${endpoint}/confirm`, { code: document.querySelector("#code").value });
  clearPairing();
  document.querySelector("#content").hidden = true;
  history.replaceState(null, "", "/");
  report(`Recovery completed for ${data.username}. Sign in again with your updated credentials. All previous sessions were signed out.`);
  if (data.recovery_codes.length) {
    document.querySelector("#recovery-codes").textContent = data.recovery_codes.join("\n");
    document.querySelector("#recovery").hidden = false;
  } else {
    document.querySelector("#done").hidden = false;
  }
});
document.querySelector("#restart").addEventListener("click", () => {
  clearPairing();
  document.querySelector("#verify").hidden = true;
  document.querySelector("#start").hidden = false;
  report("Enter your password to start again. The recovery link must still be valid.");
});
document.querySelector("#download").addEventListener("click", () => {
  const text = "Per Diem Signing replacement recovery codes\nUse each once with your password.\n\n"
    + document.querySelector("#recovery-codes").textContent;
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "per-diem-replacement-recovery-codes.txt";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.querySelector("#saved").addEventListener("change", (event) => {
  document.querySelector("#signin").hidden = !event.target.checked;
});
api(endpoint).then((data) => {
  kind = data.kind;
  document.querySelector("#account").textContent = `Account: ${data.username}`;
  document.querySelector("#instructions").textContent = kind === "password"
    ? "Choose a new password, then confirm with your existing authenticator. Your authenticator and recovery codes will not change."
    : kind === "authenticator"
      ? "Enter your current password, then pair a new authenticator. Previous authenticator and recovery codes will be replaced."
      : "Choose a new password and pair a new authenticator. Previous credentials and recovery codes will be replaced.";
  const current = kind === "authenticator";
  document.querySelector("#password-label").textContent = current ? "Current password" : "New password (at least 12 characters)";
  document.querySelector("#password").minLength = current ? 1 : 12;
  document.querySelector("#password").autocomplete = current ? "current-password" : "new-password";
  document.querySelector("#repeat-label").hidden = current;
  document.querySelector("#repeat").required = !current;
  document.querySelector("#content").hidden = false;
  report(`Recovery link expires ${new Date(data.expires * 1000).toLocaleString()}.`);
}).catch((error) => report(error.message, true));
