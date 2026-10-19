"use strict";
const status = document.querySelector("#status");
function report(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: { ...options.headers, "X-PerDiem-Request": "1" },
  });
  if (!response.ok) {
    const data = await response.json();
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      clearWorkspace();
      document.querySelector("#login").hidden = false;
      document.querySelector("#logout").hidden = true;
    }
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
function clearWorkspace() {
  document.querySelector("#workspace").hidden = true;
  document.querySelector("#requests").replaceChildren();
  document.querySelector("#created").hidden = true;
  document.querySelector("#link").value = "";
  document.querySelector("#open").removeAttribute("href");
  document.querySelector("#create").reset();
}
function clearPairing() {
  document.querySelector("#pairing").hidden = true;
  document.querySelector("#qr").removeAttribute("src");
  document.querySelector("#secret").textContent = "";
  document.querySelector("#confirm").reset();
}
async function jsonApi(path, body) {
  return (await api(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })).json();
}
async function enterWorkspace() {
  await refresh();
  document.querySelector("#workspace").hidden = false;
  document.querySelector("#login").hidden = true;
  document.querySelector("#setup").hidden = true;
  document.querySelector("#logout").hidden = false;
}
function submitForm(id, action) {
  document.querySelector(id).addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    button.disabled = true;
    try { await action(event.target); }
    catch (error) { report(error.message, true); }
    finally { button.disabled = false; }
  });
}
submitForm("#enroll", async (form) => {
  const password = document.querySelector("#new-password").value;
  if (password !== document.querySelector("#confirm-password").value) {
    throw new Error("Passwords do not match.");
  }
  const data = await jsonApi("/api/auth/enroll", {
    key: document.querySelector("#key").value.trim(), password,
  });
  form.reset();
  form.hidden = true;
  document.querySelector("#qr").src = `data:image/png;base64,${data.qr}`;
  document.querySelector("#secret").textContent = data.secret;
  document.querySelector("#pairing").hidden = false;
  report("Scan the QR code, then enter your authenticator code to finish.");
});
submitForm("#confirm", async () => {
  const data = await jsonApi("/api/auth/confirm", { code: document.querySelector("#setup-code").value });
  clearPairing();
  document.querySelector("#setup").hidden = true;
  document.querySelector("#recovery-codes").textContent = data.recovery_codes.join("\n");
  document.querySelector("#recovery").hidden = false;
  document.querySelector("#logout").hidden = false;
  report("Authenticator enabled. The old administrator key is disabled. Save your recovery codes.");
});
document.querySelector("#restart-setup").addEventListener("click", () => {
  clearPairing();
  document.querySelector("#enroll").hidden = false;
  report("Enter your setup key and password again to generate a new QR code.");
});
submitForm("#login", async (form) => {
  const data = await jsonApi("/api/auth/login", {
    password: document.querySelector("#password").value,
    code: document.querySelector("#login-code").value.trim(),
  });
  form.reset();
  await enterWorkspace();
  report(data.used_recovery_code
    ? "Signed in with a recovery code. That code cannot be reused. Keep your remaining recovery codes safe."
    : "Signed in. This session expires in eight hours.");
});
document.querySelector("#save-recovery").addEventListener("click", () => {
  const text = "Per Diem Signing recovery codes\nEach code works once, with your administrator password.\n\n"
    + document.querySelector("#recovery-codes").textContent;
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "per-diem-recovery-codes.txt";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.querySelector("#saved-recovery").addEventListener("change", (event) => {
  document.querySelector("#continue").disabled = !event.target.checked;
});
document.querySelector("#continue").addEventListener("click", async () => {
  try {
    await enterWorkspace();
    document.querySelector("#recovery").hidden = true;
    document.querySelector("#recovery-codes").textContent = "";
    report("Ready. Use your password and authenticator code to sign in on other devices.");
  } catch (error) { report(error.message, true); }
});
document.querySelector("#logout").addEventListener("click", async () => {
  try {
    await jsonApi("/api/auth/logout", {});
    clearWorkspace();
    clearPairing();
    document.querySelector("#recovery").hidden = true;
    document.querySelector("#recovery-codes").textContent = "";
    document.querySelector("#logout").hidden = true;
    document.querySelector("#login").hidden = false;
    report("Signed out.");
  } catch (error) { report(error.message, true); }
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
api("/api/auth/status").then(async (response) => {
  const state = await response.json();
  if (state.authenticated) {
    await enterWorkspace();
    report("Signed in.");
  } else if (state.configured) {
    document.querySelector("#login").hidden = false;
    report("Sign in with your password and authenticator code.");
  } else {
    document.querySelector("#setup").hidden = false;
    report("One-time administrator setup is required.");
  }
}).catch((error) => report(`Could not load login status: ${error.message}`, true));
