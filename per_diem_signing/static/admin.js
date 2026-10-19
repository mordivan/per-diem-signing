"use strict";
const status = document.querySelector("#status");
const invitationToken = location.pathname.startsWith("/join/") ? location.pathname.split("/").pop() : null;
let isSuperuser = false;
function showTab(users) {
  const showUsers = users && isSuperuser;
  document.querySelector("#team").hidden = !showUsers;
  document.querySelector("#requests-panel").hidden = showUsers;
  document.querySelector("#users-tab").setAttribute("aria-selected", String(showUsers));
  document.querySelector("#requests-tab").setAttribute("aria-selected", String(!showUsers));
  document.querySelector("#users-tab").tabIndex = showUsers ? 0 : -1;
  document.querySelector("#requests-tab").tabIndex = showUsers ? -1 : 0;
}
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
    if (response.status === 401 && !["/api/auth/login", "/api/auth/enroll", "/api/auth/confirm"].includes(path)
        && !path.startsWith("/api/auth/invitation/")) {
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
  document.querySelector("#account").textContent = `Signed in as ${data.user.username} (${data.user.is_superuser ? "superuser - all requests" : "your requests only"})`;
  isSuperuser = data.user.is_superuser;
  document.querySelector("#users-tab").hidden = !isSuperuser;
  if (!isSuperuser) showTab(false);
  document.querySelector("#network").textContent = data.local_only
    ? "LOCAL TEST ONLY: these links do not work on another phone. Configure an approved HTTPS public URL before sending them."
    : `Signing address: ${data.public_url}. Verify a link on cellular before sending requests.`;
  const container = document.querySelector("#requests");
  container.replaceChildren();
  for (const item of data.requests) {
    const article = document.createElement("article");
    const title = document.createElement("strong");
    title.textContent = `${item.details.payee} - Job ${item.details.job_number}${data.user.is_superuser ? ` - Owner: ${item.owner}` : ""}`;
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
  document.querySelector("#team").hidden = true;
  isSuperuser = false;
  document.querySelector("#users-tab").hidden = true;
  showTab(false);
  document.querySelector("#team-list").replaceChildren();
  document.querySelector("#new-invitation").hidden = true;
  document.querySelector("#invitation-link").value = "";
  document.querySelector("#new-user-recovery").hidden = true;
  document.querySelector("#user-recovery-link").value = "";
  document.querySelector("#user-recoveries").replaceChildren();
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
  const data = invitationToken
    ? await jsonApi(`/api/auth/invitation/${encodeURIComponent(invitationToken)}/enroll`, {
      username: document.querySelector("#join-username").value.trim(),
      email: document.querySelector("#join-email").value.trim(), password,
    })
    : await jsonApi("/api/auth/enroll", { key: document.querySelector("#key").value.trim(), password });
  form.reset();
  form.hidden = true;
  document.querySelector("#qr").src = `data:image/png;base64,${data.qr}`;
  document.querySelector("#secret").textContent = data.secret;
  document.querySelector("#pairing").hidden = false;
  report("Scan the QR code, then enter your authenticator code to finish.");
});
submitForm("#confirm", async () => {
  const data = await jsonApi("/api/auth/confirm", { code: document.querySelector("#setup-code").value });
  if (invitationToken) history.replaceState(null, "", "/");
  clearPairing();
  document.querySelector("#setup").hidden = true;
  document.querySelector("#recovery-codes").textContent = data.recovery_codes.join("\n");
  document.querySelector("#recovery").hidden = false;
  document.querySelector("#logout").hidden = false;
  report("Your account and authenticator are enabled. Save your recovery codes.");
});
document.querySelector("#restart-setup").addEventListener("click", () => {
  clearPairing();
  document.querySelector("#enroll").hidden = false;
  report(invitationToken ? "Enter your chosen username and password again to generate a new QR code."
    : "Enter your setup key and password again to generate a new QR code.");
});
submitForm("#login", async (form) => {
  const data = await jsonApi("/api/auth/login", {
    username: document.querySelector("#username").value.trim(),
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
async function refreshTeam() {
  const data = await (await api("/api/auth/team")).json();
  const container = document.querySelector("#team-list");
  container.replaceChildren();
  for (const user of data.users) {
    const article = document.createElement("article");
    const text = document.createElement("p");
    text.textContent = `${user.username}: ${user.is_superuser ? "Superuser" : user.disabled ? "Access revoked" : "Active - own requests only"}`;
    article.append(text);
    const info = document.createElement("p");
    info.textContent = `Contact: ${user.email || "Not provided"} | Last login: ${user.last_login ? new Date(user.last_login * 1000).toLocaleString() : "Not recorded"} | Failed logins in 15 minutes: ${user.failed_attempts}${user.failed_attempts >= 10 ? " (locked)" : ""}`;
    article.append(info);
    if (!user.is_superuser) {
      const form = document.createElement("form");
      form.setAttribute("aria-label", `Edit ${user.username}`);
      const nameLabel = document.createElement("label");
      nameLabel.textContent = "Username";
      const name = document.createElement("input");
      name.value = user.username;
      name.required = true;
      name.minLength = 3;
      name.maxLength = 40;
      name.pattern = "[A-Za-z0-9][A-Za-z0-9._\\-]*";
      nameLabel.append(name);
      const emailLabel = document.createElement("label");
      emailLabel.textContent = "Contact email";
      const email = document.createElement("input");
      email.type = "email";
      email.maxLength = 254;
      email.value = user.email;
      emailLabel.append(email);
      const save = document.createElement("button");
      save.textContent = "Save username/email";
      form.append(nameLabel, emailLabel, save);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        save.disabled = true;
        try {
          await jsonApi(`/api/auth/users/${user.id}/profile`, { username: name.value.trim(), email: email.value.trim() });
          await refreshTeam();
          report("Profile saved. A username change signs the colleague out and revokes unused recovery links. Tell them their new username; their authenticator still works.");
        } catch (error) { report(error.message, true); }
        finally { save.disabled = false; }
      });
      article.append(form);
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = user.disabled ? `Restore access for ${user.username}` : `Revoke access for ${user.username}`;
      button.addEventListener("click", async () => {
        if (!confirm(user.disabled ? `Restore login access for ${user.username}?`
          : `Revoke access for ${user.username}? They will be signed out and blocked from login. Their requests remain available to you. Recipient signing links are not revoked.`)) return;
        button.disabled = true;
        try {
          await jsonApi(`/api/auth/users/${user.id}/${user.disabled ? "enable" : "disable"}`, {});
          await refreshTeam();
          document.querySelector("#new-user-recovery").hidden = true;
          document.querySelector("#user-recovery-link").value = "";
          report(`Access ${user.disabled ? "restored" : "revoked"} for ${user.username}. Their requests are retained.`);
        } catch (error) { button.disabled = false; report(error.message, true); }
      });
      article.append(button);
      const unlock = document.createElement("button");
      unlock.type = "button";
      unlock.textContent = "Unlock failed logins";
      unlock.addEventListener("click", async () => {
        unlock.disabled = true;
        try {
          await jsonApi(`/api/auth/users/${user.id}/unlock`, {});
          await refreshTeam();
          report(`Failed login limit cleared for ${user.username}. ${user.disabled ? "Access remains revoked until restored." : "They can retry with their password and a fresh code."}`);
        } catch (error) { report(error.message, true); }
        finally { unlock.disabled = false; }
      });
      article.append(unlock);
      if (!user.disabled) {
        const label = document.createElement("label");
        label.textContent = "Account recovery";
        const select = document.createElement("select");
        for (const [value, description] of [
          ["password", "Forgot password (existing authenticator required)"],
          ["authenticator", "Lost authenticator (current password required)"],
          ["both", "Lost password AND authenticator (full account reset)"],
        ]) {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = description;
          select.append(option);
        }
        label.append(select);
        const recover = document.createElement("button");
        recover.type = "button";
        recover.textContent = `Create recovery link for ${user.username}`;
        recover.addEventListener("click", async () => {
          const warning = select.value === "both"
            ? "This full reset link lets its holder replace BOTH password and authenticator. Verify the person's identity and share privately."
            : "Share the link only with the intended colleague after verifying their identity.";
          if (!confirm(`Create recovery link for ${user.username}? ${warning} Existing sessions will be signed out and older recovery links revoked.`)) return;
          recover.disabled = true;
          try {
            const result = await jsonApi(`/api/auth/users/${user.id}/recovery`, { kind: select.value });
            document.querySelector("#user-recovery-link").value = result.link;
            document.querySelector("#new-user-recovery").hidden = false;
            document.querySelector("#user-recovery-warning").textContent = `${user.username}: ${warning} Expires ${new Date(result.expires * 1000).toLocaleString()}. No email was sent.`;
            await refreshRecoveries();
            report("Recovery link created. Copy it now and send privately. The link works once.");
          } catch (error) { report(error.message, true); }
          finally { recover.disabled = false; }
        });
        article.append(label, recover);
      }
    }
    container.append(article);
  }
  for (const invitation of data.invitations) {
    const article = document.createElement("article");
    const text = document.createElement("p");
    const expired = invitation.expires * 1000 <= Date.now();
    const acceptedUser = data.users.find((user) => user.id === invitation.used_by);
    text.textContent = `Invitation ${invitation.id.slice(0, 8)}: ${invitation.used_by ? `Accepted by ${acceptedUser ? acceptedUser.username : "account " + invitation.used_by}` : invitation.revoked ? "Revoked" : expired ? "Expired" : "Pending"}; expires ${new Date(invitation.expires * 1000).toLocaleString()}`;
    article.append(text);
    if (!invitation.used_by && !invitation.revoked && !expired) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Revoke invitation";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await jsonApi(`/api/auth/invitations/${invitation.id}/revoke`, {});
          await refreshTeam();
          document.querySelector("#new-invitation").hidden = true;
          document.querySelector("#invitation-link").value = "";
          report("Invitation revoked.");
        } catch (error) { button.disabled = false; report(error.message, true); }
      });
      article.append(button);
    }
    container.append(article);
  }
  await refreshRecoveries();
}
async function refreshRecoveries() {
  const data = await (await api("/api/auth/recoveries")).json();
  const container = document.querySelector("#user-recoveries");
  container.replaceChildren();
  for (const item of data.recoveries) {
    const article = document.createElement("article");
    const text = document.createElement("p");
    const expired = item.expires * 1000 <= Date.now();
    text.textContent = `${item.username} - ${item.kind}: ${item.used_at ? "Completed" : item.revoked ? "Revoked" : expired ? "Expired" : "Pending"}; expires ${new Date(item.expires * 1000).toLocaleString()}`;
    article.append(text);
    if (!item.used_at && !item.revoked && !expired) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Revoke recovery link";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await jsonApi(`/api/auth/recoveries/${item.id}/revoke`, {});
          document.querySelector("#new-user-recovery").hidden = true;
          document.querySelector("#user-recovery-link").value = "";
          await refreshRecoveries();
          report("Recovery link revoked.");
        } catch (error) { button.disabled = false; report(error.message, true); }
      });
      article.append(button);
    }
    container.append(article);
  }
}
document.querySelector("#requests-tab").addEventListener("click", () => showTab(false));
document.querySelector("#users-tab").addEventListener("click", async () => {
  if (!isSuperuser) return;
  showTab(true);
  try { await refreshTeam(); }
  catch (error) { report(error.message, true); }
});
document.querySelector(".tabs").addEventListener("keydown", (event) => {
  if (!isSuperuser || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const users = event.key === "End" || (event.key !== "Home" && document.querySelector("#team").hidden);
  const tab = document.querySelector(users ? "#users-tab" : "#requests-tab");
  tab.focus();
  tab.click();
});
document.querySelector("#refresh-users").addEventListener("click", () => {
  refreshTeam().catch((error) => report(error.message, true));
});
document.querySelector("#copy-user-recovery").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(document.querySelector("#user-recovery-link").value);
    report("Recovery link copied. Send it privately to the intended colleague.");
  } catch {
    document.querySelector("#user-recovery-link").select();
    report("Clipboard unavailable. Select and copy the recovery link manually.", true);
  }
});
document.querySelector("#invite").addEventListener("click", async (event) => {
  event.target.disabled = true;
  try {
    const data = await jsonApi("/api/auth/invitations", {});
    document.querySelector("#invitation-link").value = data.link;
    document.querySelector("#new-invitation").hidden = false;
    await refreshTeam();
    report("Invitation created. Copy it now; it cannot be recovered later. Send it only to your intended colleague.");
  } catch (error) { report(error.message, true); }
  finally { event.target.disabled = false; }
});
document.querySelector("#copy-invitation").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(document.querySelector("#invitation-link").value);
    report("Invitation copied.");
  } catch {
    document.querySelector("#invitation-link").select();
    report("Clipboard unavailable. Select and copy the invitation manually.", true);
  }
});
async function initialize() {
  if (invitationToken) {
    await api(`/api/auth/invitation/${encodeURIComponent(invitationToken)}`);
    document.querySelector("#setup-title").textContent = "Accept colleague invitation";
    document.querySelector("#setup-description").textContent = "Create your own account. You can manage only the requests you create; the superuser can see all requests.";
    document.querySelector("#key-label").hidden = true;
    document.querySelector("#key").required = false;
    document.querySelector("#join-username-label").hidden = false;
    document.querySelector("#join-username").required = true;
    document.querySelector("#join-email-label").hidden = false;
    document.querySelector("#setup").hidden = false;
    report("Choose your username and password, then pair your authenticator.");
    return;
  }
  const response = await api("/api/auth/status");
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
}
initialize().catch((error) => report(`Could not load account setup: ${error.message}`, true));
