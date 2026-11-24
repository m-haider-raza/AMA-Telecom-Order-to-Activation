"use strict";

/* The page a company's point of contact opens from the approval e-mail. The signed link is the only credential: the
   page calls just the two public approval routes with it, so no account or password is needed. Like the other pages
   it is drawn from DOM nodes and textContent only, never from HTML strings. */

const LINK_KEY = "tbt-approval-link";
const LANG_KEY = "tbt-approval-lang";
// The token rides in the URL fragment, which a browser never sends to a server, so it stays out of access logs.
const LINK_HASH = /^#token=([\w.-]{20,400})$/;
const CITY_TR = { Istanbul: "İstanbul", Izmir: "İzmir" };

const TEXT = {
  en: {
    title: "Approve your order · TürkBizTel",
    skip: "Skip to the approval",
    heroTitle: "Approve your TürkBizTel order",
    heroLead: "Our service team placed an order for your company. It only starts once you say yes.",
    b1t: "You stay in control",
    b1s: "Nothing is set up for your company until you approve.",
    b2t: "Straight from your e-mail",
    b2s: "Open the link we sent you. There is no password to remember.",
    b3t: "Your answer is on record",
    b3s: "It is saved with the order, so you and the service team can always see it.",
    heroAccount: "Prefer to answer from your account?",
    signIn: "Sign in",
    checking: "Checking your link…",
    askTitle: "Approve this order?",
    askLead: "{who} placed this order for {company}. Nothing starts until you approve it.",
    whoNamed: "{name} from the TürkBizTel service team",
    whoTeam: "The TürkBizTel service team",
    team: "TürkBizTel service team",
    fOrder: "Order",
    fCompany: "Company",
    fService: "Service",
    fCity: "City",
    fBy: "Placed by",
    fAsked: "Requested",
    fUntil: "Answer by",
    approve: "Approve order",
    decline: "Decline",
    declineAsk: "Decline this order? Nothing will be set up for {company}.",
    declineYes: "Yes, decline it",
    goBack: "Go back",
    answerNote: "You are answering for {company}. Your answer is saved with the order, and the service team can see it.",
    signInAlt: "Prefer to use your account?",
    openAccount: "Open your account",
    retry: "Try again",
    approvedNowTitle: "Order approved",
    approvedNowBody: "Thank you. Your order is approved and our agents are starting on it now. You can follow its progress in your account.",
    approvedBeforeTitle: "Already approved",
    approvedBeforeBody: "This order was already approved, so there is nothing more to do. You can follow its progress in your account.",
    declinedNowTitle: "Order declined",
    declinedNowBody: "Thank you for letting us know. Nothing was set up for {company}, and the service team can see your answer.",
    declinedBeforeTitle: "Already declined",
    declinedBeforeBody: "This order was declined and nothing was set up. If that was a mistake, ask the service team to place it again.",
    withdrawnTitle: "Request withdrawn",
    withdrawnBody: "The TürkBizTel service team withdrew this request, so there is nothing to approve. Nothing was set up.",
    expiredTitle: "This request has expired",
    expiredBody: "It could be answered until {when}. Ask the TürkBizTel service team to send a new one.",
    replacedTitle: "A newer e-mail replaced this link",
    replacedBody: "Open the latest e-mail from TürkBizTel, or sign in to your account to answer.",
    missingTitle: "Open the link from your e-mail",
    missingBody: "This page opens from the personal link we e-mailed to your company's contact. Open that e-mail and select the approval link, or sign in to your account.",
    badTitle: "This link isn't valid",
    badBody: "It may have expired or been copied incompletely. Ask the TürkBizTel service team to send a new one.",
    busyTitle: "Too many attempts",
    busyBody: "Please wait {wait} and try again.",
    offlineTitle: "We couldn't load this request",
    offlineBody: "Check your connection and try again.",
    tooMany: "Too many attempts. Please wait {wait} and try again.",
    generic: "That didn't work. Please try again.",
    offline: "We couldn't reach TürkBizTel. Check your connection and try again.",
    aMoment: "a moment",
    seconds: "{n} seconds",
    minutes: "{n} minutes",
  },
  tr: {
    title: "Siparişinizi onaylayın · TürkBizTel",
    skip: "Onaya geç",
    heroTitle: "TürkBizTel siparişinizi onaylayın",
    heroLead: "Hizmet ekibimiz şirketiniz için bir sipariş oluşturdu. Siz onaylayana kadar başlamaz.",
    b1t: "Kontrol sizde",
    b1s: "Siz onaylamadan şirketiniz için hiçbir şey kurulmaz.",
    b2t: "Doğrudan e-postanızdan",
    b2s: "Size gönderdiğimiz bağlantıyı açın. Hatırlanacak bir parola yok.",
    b3t: "Yanıtınız kayda geçer",
    b3s: "Siparişle birlikte saklanır; siz ve hizmet ekibi her zaman görebilirsiniz.",
    heroAccount: "Yanıtı hesabınızdan vermek mi istersiniz?",
    signIn: "Giriş yapın",
    checking: "Bağlantınız kontrol ediliyor…",
    askTitle: "Bu sipariş onaylansın mı?",
    askLead: "{who}, {company} için bu siparişi oluşturdu. Siz onaylamadan hiçbir şey başlamaz.",
    whoNamed: "TürkBizTel hizmet ekibinden {name}",
    whoTeam: "TürkBizTel hizmet ekibi",
    team: "TürkBizTel hizmet ekibi",
    fOrder: "Sipariş",
    fCompany: "Şirket",
    fService: "Hizmet",
    fCity: "Şehir",
    fBy: "Oluşturan",
    fAsked: "Talep tarihi",
    fUntil: "Son yanıt tarihi",
    approve: "Siparişi onayla",
    decline: "Reddet",
    declineAsk: "Bu sipariş reddedilsin mi? {company} için hiçbir şey kurulmayacak.",
    declineYes: "Evet, reddet",
    goBack: "Geri dön",
    answerNote: "{company} adına yanıt veriyorsunuz. Yanıtınız siparişle birlikte saklanır ve hizmet ekibi tarafından görülebilir.",
    signInAlt: "Hesabınızı kullanmak mı istersiniz?",
    openAccount: "Hesabınızı açın",
    retry: "Yeniden dene",
    approvedNowTitle: "Sipariş onaylandı",
    approvedNowBody: "Teşekkürler. Siparişiniz onaylandı ve ajanlarımız şimdi üzerinde çalışmaya başlıyor. İlerleyişi hesabınızdan takip edebilirsiniz.",
    approvedBeforeTitle: "Zaten onaylanmış",
    approvedBeforeBody: "Bu sipariş daha önce onaylandı; yapmanız gereken başka bir şey yok. İlerleyişi hesabınızdan takip edebilirsiniz.",
    declinedNowTitle: "Sipariş reddedildi",
    declinedNowBody: "Bildirdiğiniz için teşekkürler. {company} için hiçbir şey kurulmadı ve hizmet ekibi yanıtınızı görebilir.",
    declinedBeforeTitle: "Zaten reddedilmiş",
    declinedBeforeBody: "Bu sipariş reddedildi ve hiçbir şey kurulmadı. Bu bir hataysa hizmet ekibinden siparişi yeniden oluşturmasını isteyin.",
    withdrawnTitle: "Talep geri çekildi",
    withdrawnBody: "TürkBizTel hizmet ekibi bu talebi geri çekti; onaylanacak bir şey yok. Hiçbir şey kurulmadı.",
    expiredTitle: "Bu talebin süresi doldu",
    expiredBody: "Talep {when} tarihine kadar yanıtlanabiliyordu. TürkBizTel hizmet ekibinden yenisini göndermesini isteyin.",
    replacedTitle: "Bu bağlantının yerini daha yeni bir e-posta aldı",
    replacedBody: "TürkBizTel'den gelen en son e-postayı açın veya yanıtlamak için hesabınıza giriş yapın.",
    missingTitle: "E-postanızdaki bağlantıyı açın",
    missingBody: "Bu sayfa, şirketinizin irtibat kişisine e-postayla gönderdiğimiz kişisel bağlantıyla açılır. O e-postayı açıp onay bağlantısını seçin veya hesabınıza giriş yapın.",
    badTitle: "Bu bağlantı geçerli değil",
    badBody: "Süresi dolmuş ya da eksik kopyalanmış olabilir. TürkBizTel hizmet ekibinden yenisini göndermesini isteyin.",
    busyTitle: "Çok fazla deneme",
    busyBody: "Lütfen {wait} bekleyip yeniden deneyin.",
    offlineTitle: "Bu talep yüklenemedi",
    offlineBody: "Bağlantınızı kontrol edip yeniden deneyin.",
    tooMany: "Çok fazla deneme. Lütfen {wait} bekleyip yeniden deneyin.",
    generic: "İşlem tamamlanamadı. Lütfen yeniden deneyin.",
    offline: "TürkBizTel'e ulaşılamadı. Bağlantınızı kontrol edip yeniden deneyin.",
    aMoment: "biraz",
    seconds: "{n} saniye",
    minutes: "{n} dakika",
  },
};

const OUTCOMES = {
  approved: { tone: "ok", icon: "circle-check" },
  declined: { tone: "", icon: "x" },
  withdrawn: { tone: "", icon: "info" },
  expired: { tone: "warn", icon: "clock" },
  replaced: { tone: "info", icon: "mail" },
};
const PROBLEMS = {
  missing: { tone: "info", icon: "mail", retry: false },
  bad: { tone: "bad", icon: "alert", retry: false },
  busy: { tone: "warn", icon: "clock", retry: true },
  offline: { tone: "warn", icon: "alert", retry: true },
};

const stage = $("#stage");
let lang = "en";
let chosen = false;
let token = "";
let current = null;

/* ---- wording ---- */

function t(key, vars = {}) {
  const text = TEXT[lang][key] ?? TEXT.en[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? "");
}

function when(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(lang === "tr" ? "tr-TR" : "en-GB", {
    day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
  }).format(date);
}

const cityName = (city) => (lang === "tr" ? CITY_TR[city] || city : city);

function waitText(seconds) {
  if (!seconds) return t("aMoment");
  return seconds > 90 ? t("minutes", { n: Math.ceil(seconds / 60) }) : t("seconds", { n: seconds });
}

function setLang(next, remember = false) {
  lang = next === "tr" ? "tr" : "en";
  if (remember) {
    chosen = true;
    try {
      sessionStorage.setItem(LANG_KEY, lang);
    } catch {
      // Storage is blocked: the choice simply is not kept across a reload.
    }
  }
  document.documentElement.lang = lang;
  document.title = t("title");
  for (const node of $$("[data-t]")) node.textContent = t(node.dataset.t);
  for (const button of $$(".lang-switch [data-lang]")) {
    button.setAttribute("aria-pressed", String(button.dataset.lang === lang));
  }
  if (current) draw(false);
}

function startingLanguage() {
  try {
    const saved = sessionStorage.getItem(LANG_KEY);
    if (saved === "en" || saved === "tr") {
      chosen = true;
      return saved;
    }
  } catch {
    // Storage is blocked, so fall through to the browser's language.
  }
  return (navigator.language || "").toLowerCase().startsWith("tr") ? "tr" : "en";
}

/* ---- the link ---- */

/* Returns the token from the address, keeping it for this tab and clearing it from the address bar, so a reload
   still works and the link is not left in view. "bad" means the address carried a token that is not well formed. */
function readLink() {
  const match = LINK_HASH.exec(location.hash);
  if (match) {
    try {
      sessionStorage.setItem(LINK_KEY, match[1]);
    } catch {
      // Storage is blocked: the token stays in memory for this page view only.
    }
    history.replaceState(null, "", location.pathname);
    return { token: match[1] };
  }
  if (location.hash.startsWith("#token=")) return { bad: true };
  try {
    return { token: sessionStorage.getItem(LINK_KEY) || "" };
  } catch {
    return { token: "" };
  }
}

/* ---- drawing ---- */

function fact(label, value, mono = false) {
  return h("div", {}, h("dt", { text: label }), h("dd", { class: mono && "mono", text: value }));
}

const signInHint = () => h("p", { class: "alt" }, t("signInAlt"), " ", h("a", { href: "/login", text: t("signIn") }));

function resultIcon(look) {
  return h("span", { class: `result-icon ${look.tone}`.trim() }, icon(look.icon));
}

function problemView({ problem, retryAfter }) {
  const look = PROBLEMS[problem];
  return [
    resultIcon(look),
    h("h2", { tabindex: "-1", text: t(`${problem}Title`) }),
    h("p", { class: "sub", text: t(`${problem}Body`, { wait: waitText(retryAfter) }) }),
    look.retry && h("div", { class: "actions" },
      h("button", { type: "button", class: "btn", onclick: (event) => busy(event.currentTarget, load) },
        icon("refresh"), t("retry"))),
    signInHint(),
  ];
}

function doneView({ outcome, fresh, info }) {
  const look = OUTCOMES[outcome];
  const key = outcome === "approved" || outcome === "declined" ? outcome + (fresh ? "Now" : "Before") : outcome;
  return [
    resultIcon(look),
    h("h2", { tabindex: "-1", text: t(`${key}Title`) }),
    h("p", { class: "sub", text: t(`${key}Body`, { company: info.company, when: when(info.expiresAt) }) }),
    info.orderId && h("dl", { class: "facts invite-facts" },
      fact(t("fOrder"), info.orderId, true), fact(t("fCompany"), info.company), fact(t("fService"), info.product)),
    h("div", { class: "actions" },
      h("a", { class: `btn${outcome === "approved" ? "" : " secondary"}`, href: "/login" }, icon("user"), t("openAccount"))),
  ];
}

function failure(error) {
  if (error.status === 429) return t("tooMany", { wait: waitText(error.retryAfter) });
  if (!error.status || error.status >= 500) return t("offline");
  return lang === "en" ? error.message : t("generic");
}

/* Approve and Decline. Declining asks first, in place, because it cannot be taken back from this page. */
function decisionBlock(info) {
  const message = h("p", { class: "msg error", id: "decide-msg", role: "alert", hidden: true });
  const area = h("div", { class: "decision" });

  const reply = (decision) => (event) => busy(event.currentTarget, async () => {
    say(message, "");
    try {
      const result = await api("/api/approvals/decide", { method: "POST", body: { token, decision } });
      show({ kind: "done", outcome: result.status, fresh: true, info: { ...info, ...result } });
    } catch (error) {
      // Someone answered, withdrew or renewed the request meanwhile: show how it stands now.
      if (error.status === 409) return load();
      if (error.status === 404) return show({ kind: "problem", problem: "bad" });
      return say(message, failure(error), "error");
    }
    return undefined;
  });

  const approve = h("button", { type: "button", class: "btn block", onclick: reply("approve") },
    icon("circle-check"), t("approve"));
  const decline = h("button", { type: "button", class: "btn secondary block", onclick: () => ask(true) },
    icon("x"), t("decline"));
  const confirmBox = h("div", {
    class: "decision-confirm", role: "group", tabindex: "-1", "aria-labelledby": "decline-question",
    onkeydown: (event) => { if (event.key === "Escape") ask(false); },
  },
  h("p", { id: "decline-question", text: t("declineAsk", { company: info.company }) }),
  h("div", { class: "decision-row" },
    h("button", { type: "button", class: "btn secondary", onclick: reply("decline") }, t("declineYes")),
    h("button", { type: "button", class: "btn ghost", onclick: () => ask(false) }, t("goBack"))));

  function ask(on) {
    say(message, "");
    area.replaceChildren(...(on ? [confirmBox] : [approve, decline]));
    (on ? confirmBox : decline).focus();
  }

  area.append(approve, decline);
  return [area, message];
}

function pendingView(info) {
  const who = info.requestedBy ? t("whoNamed", { name: info.requestedBy }) : t("whoTeam");
  return [
    h("h2", { tabindex: "-1", text: t("askTitle") }),
    h("p", { class: "sub", text: t("askLead", { who, company: info.company }) }),
    h("dl", { class: "facts invite-facts" },
      fact(t("fOrder"), info.orderId, true), fact(t("fCompany"), info.company), fact(t("fService"), info.product),
      fact(t("fCity"), cityName(info.city)), fact(t("fBy"), info.requestedBy || t("team")),
      fact(t("fAsked"), when(info.requestedAt)), fact(t("fUntil"), when(info.expiresAt))),
    decisionBlock(info),
    h("p", { class: "hint mt", text: t("answerNote", { company: info.company }) }),
    signInHint(),
  ];
}

const VIEWS = {
  loading: () => h("p", { class: "sso-wait", role: "status" },
    h("span", { class: "spinner", "aria-hidden": "true" }), h("span", { text: t("checking") })),
  problem: problemView,
  pending: ({ info }) => pendingView(info),
  done: doneView,
};

function draw(focus = true) {
  stage.replaceChildren(...[VIEWS[current.kind](current)].flat(Infinity).filter(Boolean));
  const heading = $('h2[tabindex="-1"]', stage);
  if (focus && heading) heading.focus();
}

function show(next, focus = true) {
  current = next;
  draw(focus);
}

/* ---- talking to the server ---- */

function problemFor(error) {
  if (error.status === 429) return { kind: "problem", problem: "busy", retryAfter: error.retryAfter };
  if (error.status >= 400 && error.status < 500) return { kind: "problem", problem: "bad" };
  return { kind: "problem", problem: "offline" };
}

async function load() {
  show({ kind: "loading" }, false);
  try {
    const info = await api("/api/approvals/check", { method: "POST", body: { token } });
    if (!chosen && info.language) setLang(info.language);
    if (info.status === "pending") show({ kind: "pending", info });
    else if (OUTCOMES[info.status]) show({ kind: "done", outcome: info.status, fresh: false, info });
    else show({ kind: "problem", problem: "bad" });
  } catch (error) {
    show(problemFor(error));
  }
}

for (const button of $$(".lang-switch [data-lang]")) {
  button.addEventListener("click", () => setLang(button.dataset.lang, true));
}

setLang(lang);
api("/api/auth/me").then(me => {
  if (!me.isolatedArchitecture) return;
  Object.assign(TEXT.en, {
    b2t: "Synthetic approval only",
    b2s: "No email is sent. Approve in your customer account or use a shared test link.",
    replacedTitle: "A newer approval request replaced this link",
    replacedBody: "Use the latest test link, or sign in to your account to answer.",
    missingTitle: "Approve in your account",
    missingBody: "No approval email is sent in this isolated application. Sign in to your customer account to approve, or use a test link shared by the service team.",
  });
  Object.assign(TEXT.tr, {
    b2t: "Yalnızca sentetik onay",
    b2s: "E-posta gönderilmez. Müşteri hesabınızda onaylayın veya paylaşılan test bağlantısını kullanın.",
    replacedTitle: "Bu bağlantının yerini yeni bir onay talebi aldı",
    replacedBody: "En son test bağlantısını kullanın veya yanıtlamak için hesabınıza giriş yapın.",
    missingTitle: "Hesabınızda onaylayın",
    missingBody: "Bu izole uygulamada onay e-postası gönderilmez. Onaylamak için müşteri hesabınıza giriş yapın veya hizmet ekibinin paylaştığı test bağlantısını kullanın.",
  });
  setLang(startingLanguage());
}).catch(error => console.error("Could not determine approval delivery mode", error));
const link = readLink();
if (link.bad) show({ kind: "problem", problem: "bad" }, false);
else if (!link.token) show({ kind: "problem", problem: "missing" }, false);
else {
  token = link.token;
  load();
}
