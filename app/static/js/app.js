/* OLX Monitor — vanilla JS */

// placeholder para imagens quebradas (anti-hotlink ou removidas)
const PLACEHOLDER =
    "data:image/svg+xml," +
    encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">' +
        '<rect width="100%" height="100%" fill="#e8e8e8"/>' +
        '<text x="50%" y="50%" font-family="sans-serif" font-size="20" fill="#999" text-anchor="middle">sem imagem</text>' +
        "</svg>"
    );

document.addEventListener(
    "error",
    (e) => {
        const t = e.target;
        if (t.tagName === "IMG" && !t.dataset.fallback && t.classList.contains("app-img")) {
            t.dataset.fallback = "1";
            t.src = PLACEHOLDER;
        }
    },
    true
);

// ---- Tema escuro/claro ----
function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) {
        btn.textContent = theme === "dark" ? "☀️" : "🌙";
        btn.setAttribute("aria-label", theme === "dark" ? "Tema claro" : "Tema escuro");
    }
}
(function initTheme() {
    const saved = localStorage.getItem("olx-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(saved || (prefersDark ? "dark" : "light"));
    const btn = document.getElementById("theme-toggle");
    if (btn) {
        btn.addEventListener("click", () => {
            const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
            localStorage.setItem("olx-theme", next);
            applyTheme(next);
        });
    }
})();

// ---- Galeria: troca a imagem principal pelos thumbnails ----
document.addEventListener("DOMContentLoaded", () => {
    const main = document.getElementById("gallery-main");
    const thumbs = document.querySelectorAll(".gallery-thumbs img");
    if (main && thumbs.length) {
        thumbs.forEach((th) => {
            th.addEventListener("click", () => {
                main.src = th.dataset.full;
                thumbs.forEach((x) => x.classList.toggle("active", x === th));
            });
        });
    }

    // esconde filtros em telas pequenas por padrão
    const toggle = document.getElementById("filters-toggle");
    const form = document.getElementById("filters-form");
    if (toggle && form) {
        const narrow = window.matchMedia("(max-width: 720px)");
        const setOpen = (open) => {
            form.style.display = open ? "" : "none";
            toggle.textContent = open ? "Ocultar filtros ▲" : "Filtros ▼";
        };
        setOpen(!narrow.matches);
        toggle.addEventListener("click", () => setOpen(form.style.display === "none"));
    }

    initRunPage();
    initAutostart();
    initAddAd();
    initAdDetail();
    initCpuFamilyFilter();
});

// ---- Página /run: switch de autorun ----
function initAutostart() {
    const toggle = document.getElementById("autostart-toggle");
    if (!toggle) return;

    const statusEl = document.getElementById("autostart-status");
    const initial = window.OLX_AUTOSTART || {};

    function setStatus(msg) {
        statusEl.textContent = msg;
        statusEl.hidden = !msg;
    }

    if (initial.enabled && !initial.running) {
        setStatus("⚠ scheduler do processo não está ativo (AUTORUN_ENABLED=0). Reinicie a app.");
    }

    toggle.addEventListener("change", async () => {
        toggle.disabled = true;
        setStatus("salvando…");
        try {
            const resp = await fetch("/api/autostart", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: toggle.checked }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                toggle.checked = !toggle.checked;
                setStatus(data.error || "erro ao salvar");
                return;
            }
            setStatus(data.warning || "autorun " + (data.enabled ? "ligado ✓" : "desligado"));
        } catch (e) {
            toggle.checked = !toggle.checked;
            setStatus("falha ao salvar");
        } finally {
            toggle.disabled = false;
        }
    });
}

// ---- Página /run: dispara e acompanha a execução ----
let runPollTimer = null;
let runLogCount = 0;

function initRunPage() {
    const form = document.getElementById("run-form");
    if (!form) return;

    const btn = document.getElementById("run-button");
    const errEl = document.getElementById("run-error");
    const panel = document.getElementById("run-progress-panel");
    const progress = document.getElementById("run-progress");
    const statusEl = document.getElementById("run-status");
    const msgEl = document.getElementById("run-message");
    const logEl = document.getElementById("run-log");
    const runIdEl = document.getElementById("run-id");

    const VALID_STEPS = window.OLX_RUN_STEPS || [];

    initTermsEditor(errEl);

    function setBusy(busy) {
        btn.disabled = busy;
        btn.textContent = busy ? "Rodando…" : "Rodar";
    }

    function showError(msg) {
        errEl.textContent = msg;
        errEl.hidden = false;
        setBusy(false);
    }

    function renderLog(job) {
        const lines = job.log || [];
        const fresh = lines.slice(runLogCount);
        runLogCount = lines.length;
        if (fresh.length) logEl.textContent += fresh.join("\n") + "\n";
        logEl.scrollTop = logEl.scrollHeight;
    }

    function renderJob(job) {
        panel.hidden = false;
        runIdEl.textContent = "#" + job.id;
        statusEl.textContent = job.status;
        statusEl.dataset.status = job.status;
        progress.value = job.percent || 0;
        msgEl.textContent = job.message || "";
        renderLog(job);
        return job.status;
    }

    function stopPolling() {
        if (runPollTimer) {
            clearInterval(runPollTimer);
            runPollTimer = null;
        }
    }

    function poll(id) {
        stopPolling();
        runPollTimer = setInterval(async () => {
            try {
                const resp = await fetch(`/api/runs/${id}`);
                const job = await resp.json();
                if (!resp.ok) {
                    stopPolling();
                    showError(job.error || "erro ao consultar a run");
                    return;
                }
                const status = renderJob(job);
                if (status === "done" || status === "error") {
                    stopPolling();
                    setBusy(false);
                }
            } catch (e) {
                stopPolling();
                showError("falha ao consultar a run");
            }
        }, 1000);
    }

    form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        errEl.hidden = true;

        const terms = document.getElementById("run-terms")
            .value.split("\n").map((s) => s.trim()).filter(Boolean);
        const region = document.getElementById("run-region").value.trim() || "estado-sp";
        const steps = [...form.querySelectorAll('input[name="steps"]:checked')]
            .map((c) => c.value).filter((s) => VALID_STEPS.includes(s));

        if (!terms.length) {
            showError("digite ao menos um termo de busca");
            return;
        }
        if (!steps.length) {
            showError("selecione ao menos uma etapa");
            return;
        }

        setBusy(true);
        runLogCount = 0;
        logEl.textContent = "";
        panel.hidden = true;

        try {
            const resp = await fetch("/api/runs", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ terms, region, steps }),
            });
            const data = await resp.json();
            if (resp.status === 409) {
                showError(data.error || "já existe uma run ativa");
                return;
            }
            if (!resp.ok) {
                showError(data.error || "erro ao iniciar a run");
                return;
            }
            poll(data.id);
        } catch (e) {
            showError("falha ao iniciar a run");
        }
    });

    // se já existe uma run ativa (ex.: outra aba), acompanha-a
    fetch("/api/runs/current").then((r) => r.json()).then((job) => {
        if (job && job.status !== "done" && job.status !== "error") {
            setBusy(true);
            renderJob(job);
            poll(job.id);
        }
    }).catch(() => {});
}

// ---- Página /run: cadastro manual de anúncio por link ----
function initAddAd() {
    const form = document.getElementById("add-ad-form");
    if (!form) return;

    const urlEl = document.getElementById("add-ad-url");
    const procEl = document.getElementById("add-ad-process");
    const btn = document.getElementById("add-ad-button");
    const errEl = document.getElementById("add-ad-error");
    const resultEl = document.getElementById("add-ad-result");

    function setBusy(busy) {
        btn.disabled = busy;
        btn.textContent = busy ? "Cadastrando…" : "Cadastrar";
    }

    function formatPrice(cents) {
        if (cents == null) return "";
        return "R$ " + (cents / 100).toFixed(2).replace(".", ",");
    }

    form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        errEl.hidden = true;
        resultEl.hidden = true;
        const url = urlEl.value.trim();
        if (!url) {
            errEl.textContent = "cole o link do anúncio";
            errEl.hidden = false;
            return;
        }
        setBusy(true);
        try {
            const resp = await fetch("/api/ads/import", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, process: procEl.checked }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                errEl.textContent = data.error || "erro ao cadastrar";
                errEl.hidden = false;
                return;
            }
            const verb = data.created ? "cadastrado" : "atualizado";
            const specs = data.processed ? "specs extraídas" : "specs pendentes";
            const price = data.ad && data.ad.price_cents != null
                ? " · " + formatPrice(data.ad.price_cents) : "";
            resultEl.textContent = `Anúncio ${verb} ✓ (${specs}${price})`;
            resultEl.innerHTML += ` — <a href="/ads/${data.ad.id}">ver anúncio #${data.ad.id}</a>`;
            resultEl.hidden = false;
            urlEl.value = "";
        } catch (e) {
            errEl.textContent = "falha ao cadastrar";
            errEl.hidden = false;
        } finally {
            setBusy(false);
        }
    });
}

// ---- Página /ads/<id>: toggle "Disponível" (ocultar/exibir manualmente) ----
function initAdDetail() {
    const toggle = document.getElementById("ad-available-toggle");
    if (!toggle) return;

    const statusEl = document.getElementById("ad-toggle-status");
    const adId = toggle.dataset.adId;
    const badges = () => {
        let el = document.querySelector("span.badge.oculto");
        if (toggle.checked && el) el.remove();
        if (!toggle.checked && !el) {
            const p = document.createElement("p");
            p.innerHTML = '<span class="badge oculto">oculto manualmente</span>';
            document.querySelector("h1.page-title").after(p);
        }
    };

    function setStatus(msg) {
        statusEl.textContent = msg;
        statusEl.hidden = !msg;
        if (msg) setTimeout(() => { statusEl.hidden = true; }, 2500);
    }

    toggle.addEventListener("change", async () => {
        toggle.disabled = true;
        try {
            const resp = await fetch(`/api/ads/${adId}/disabled`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ disabled: !toggle.checked }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                toggle.checked = !toggle.checked;
                setStatus(data.error || "erro ao salvar");
                return;
            }
            badges();
            setStatus(toggle.checked ? "disponível ✓" : "oculto ✓");
        } catch (e) {
            toggle.checked = !toggle.checked;
            setStatus("falha ao salvar");
        } finally {
            toggle.disabled = false;
        }
    });
}

// ---- Filtros: geração é dependente da família de CPU ----
function initCpuFamilyFilter() {
    const sel = document.getElementById("cpu_family");
    const form = document.getElementById("filters-form");
    if (!sel || !form) return;
    sel.addEventListener("change", () => {
        // limpa a geração para não mandar valor fora da faixa da nova família
        const gen = document.getElementById("gen_min");
        if (gen) gen.value = "";
        form.submit();
    });
}

// ---- Termos de busca: bloqueados por padrão, Editar/Salvar/Cancelar ----
function initTermsEditor(errEl) {
    const area = document.getElementById("run-terms");
    const editBtn = document.getElementById("terms-edit");
    const saveBtn = document.getElementById("terms-save");
    const cancelBtn = document.getElementById("terms-cancel");
    const savedEl = document.getElementById("terms-saved");
    if (!area || !editBtn) return;

    let saved = (window.OLX_RUN_TERMS || []).join("\n");

    function setReadonly(on) {
        area.readOnly = on;
        editBtn.hidden = on ? false : true;
        saveBtn.hidden = on ? true : false;
        cancelBtn.hidden = on ? true : false;
        if (!on) area.focus();
    }

    function flashSaved() {
        savedEl.hidden = false;
        setTimeout(() => { savedEl.hidden = true; }, 2000);
    }

    editBtn.addEventListener("click", () => setReadonly(false));

    cancelBtn.addEventListener("click", () => {
        area.value = saved;
        setReadonly(true);
        if (errEl) errEl.hidden = true;
    });

    saveBtn.addEventListener("click", async () => {
        const terms = area.value.split("\n").map((s) => s.trim()).filter(Boolean);
        const region = document.getElementById("run-region").value.trim() || "estado-sp";
        try {
            const resp = await fetch("/api/runs/terms", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ terms, region }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                if (errEl) { errEl.textContent = data.error || "erro ao salvar termos"; errEl.hidden = false; }
                return;
            }
            saved = (data.terms || []).join("\n");
            setReadonly(true);
            flashSaved();
            if (errEl) errEl.hidden = true;
        } catch (e) {
            if (errEl) { errEl.textContent = "falha ao salvar termos"; errEl.hidden = false; }
        }
    });
}

