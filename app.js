function () {
    let root = document.getElementById("rp-root");

    const VIEW_TITLES = {
        dashboard: "Dashboard",
        products: "Products",
        orders: "Orders",
        assistant: "Assistant",
        profile: "Profile",
    };

    const STATUS_OPTIONS = ["pending", "processing", "shipped", "delivered", "cancelled"];
    const STORAGE_KEY = "shopy_retailer_session_token";

    const state = {
        bridgeAction: null,
        bridgePayloadIn: null,
        bridgeOutput: null,
        bridgeSubmit: null,
        requestSeq: 0,
        bridgeQueue: Promise.resolve(),
        sessionToken: "",
        user: null,
        pendingToken: "",
        currentView: "dashboard",
        loaded: {
            dashboard: false,
            products: false,
            orders: false,
            assistant: false,
            profile: false,
        },
        products: [],
        categories: [],
        orders: [],
        typingNode: null,
    };

    let isRegister = false;
    let lRole = "customer";
    let rRole = "customer";

    function byId(id) {
        return document.getElementById(id);
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function asText(value) {
        return String(value == null ? "" : value);
    }

    function fmtMoney(value) {
        const num = Number(value || 0);
        return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function setBridgeValue(node, value) {
        if (!node) {
            return;
        }
        node.value = value;
        node.dispatchEvent(new Event("input", { bubbles: true }));
        node.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function findBridgeInput(idBase) {
        return (
            document.querySelector("#" + idBase + " textarea") ||
            document.querySelector("#" + idBase + " input") ||
            document.querySelector("textarea#" + idBase) ||
            document.querySelector("input#" + idBase)
        );
    }

    function findBridgeButton(idBase) {
        return (
            document.querySelector("#" + idBase + " button") ||
            document.querySelector("button#" + idBase)
        );
    }

    function ensureBridgeRefs() {
        state.bridgeAction = findBridgeInput("bridge-action");
        state.bridgePayloadIn = findBridgeInput("bridge-payload-in");
        state.bridgeOutput = findBridgeInput("bridge-output");
        state.bridgeSubmit = findBridgeButton("bridge-submit");

        return Boolean(
            state.bridgeAction && state.bridgePayloadIn && state.bridgeOutput && state.bridgeSubmit
        );
    }

    function waitForBridge() {
        return new Promise((resolve, reject) => {
            let tries = 0;
            const loop = () => {
                if (ensureBridgeRefs()) {
                    resolve();
                    return;
                }
                tries += 1;
                if (tries > 80) {
                    reject(new Error("Could not connect frontend bridge."));
                    return;
                }
                setTimeout(loop, 120);
            };
            loop();
        });
    }

    function callBridgeRaw(action, data) {
        return new Promise((resolve, reject) => {
            if (!ensureBridgeRefs()) {
                reject(new Error("Bridge is not ready."));
                return;
            }

            const requestId = String(Date.now()) + "-" + String(++state.requestSeq);
            const payload = JSON.stringify({ request_id: requestId, data: data || {} });

            setBridgeValue(state.bridgeAction, action);
            setBridgeValue(state.bridgePayloadIn, payload);
            setBridgeValue(state.bridgeOutput, "");

            if (!state.bridgeSubmit || typeof state.bridgeSubmit.click !== "function") {
                reject(new Error("Bridge submit button missing."));
                return;
            }
            state.bridgeSubmit.click();

            const timeout = setTimeout(() => {
                clearInterval(timer);
                reject(new Error("Backend request timed out."));
            }, 26000);

            const timer = setInterval(() => {
                const raw = asText(state.bridgeOutput && state.bridgeOutput.value).trim();
                if (!raw) {
                    return;
                }

                let parsed;
                try {
                    parsed = JSON.parse(raw);
                } catch (_err) {
                    return;
                }

                if (parsed.request_id && parsed.request_id !== requestId) {
                    return;
                }

                clearTimeout(timeout);
                clearInterval(timer);

                if (parsed.ok) {
                    resolve(parsed.data || {});
                } else {
                    reject(new Error(parsed.error || "Request failed."));
                }
            }, 120);
        });
    }

    function callBridge(action, data) {
        const run = () => callBridgeRaw(action, data);
        const chained = state.bridgeQueue.then(run, run);
        state.bridgeQueue = chained.catch(() => null);
        return chained;
    }

    function toast(message, kind) {
        const wrap = byId("rp-toast-wrap");
        if (!wrap) {
            return;
        }
        const t = document.createElement("div");
        t.className = "rp-toast " + (kind === "ok" ? "ok" : "err");
        t.textContent = asText(message || (kind === "ok" ? "Done." : "Something went wrong."));
        wrap.appendChild(t);
        setTimeout(() => {
            if (t.parentNode) {
                t.parentNode.removeChild(t);
            }
        }, 3200);
    }

    function setBusy(show, text) {
        const node = byId("rp-busy");
        const txt = byId("rp-busy-text");
        if (!node || !txt) {
            return;
        }
        txt.textContent = asText(text || "Loading...");
        if (show) {
            node.classList.add("active");
        } else {
            node.classList.remove("active");
        }
    }

    function clearAuthMessage() {
        root.querySelectorAll(".runtime-auth-msg").forEach((node) => {
            if (node && node.parentNode) {
                node.parentNode.removeChild(node);
            }
        });
    }

    function getAuthForm(kind) {
        const action = kind === "register" ? "/register" : "/login";
        return root.querySelector('form[action="' + action + '"]');
    }

    function setAuthMessage(message, isError, kind) {
        clearAuthMessage();
        if (!message) {
            return;
        }

        const targetKind = kind || (isRegister ? "register" : "login");
        const form = getAuthForm(targetKind);
        if (!form) {
            toast(message, isError ? "err" : "ok");
            return;
        }

        const box = document.createElement("div");
        box.className = "form-error runtime-auth-msg";
        box.textContent = asText(message);
        form.insertBefore(box, form.firstChild);
    }

    function updatePill(role) {
      const leftPill = byId("leftPill");
      const pillText = byId("pillText");
      if (!leftPill || !pillText) {
          return;
      }
      leftPill.className = "left-role-pill " + role;
      if (role === "retailer") {
        pillText.textContent = isRegister ? "Registering as Retailer" : "Signing in as Retailer";
      } else {
        pillText.textContent = isRegister ? "Registering as Customer" : "Shopping as Customer";
      }
    }

    function animateTextChange(callback) {
      const textStage = byId("textStage");
      if (!textStage) {
          callback();
          return;
      }
      textStage.classList.add("fade-out");
      setTimeout(() => {
        callback();
        textStage.classList.remove("fade-out");
        textStage.classList.add("fade-in");
        setTimeout(() => textStage.classList.remove("fade-in"), 400);
      }, 300);
    }

    function setMode(register) {
      const slider = byId("slider");
      const tabLogin = byId("tabLogin");
      const tabRegister = byId("tabRegister");
      const leftTitle = byId("leftTitle");
      const leftDesc = byId("leftDesc");

      isRegister = Boolean(register);
      if (slider) {
          slider.style.transform = isRegister ? "translateX(-50%)" : "translateX(0%)";
      }
      if (tabLogin) {
          tabLogin.classList.toggle("active", !isRegister);
      }
      if (tabRegister) {
          tabRegister.classList.toggle("active", isRegister);
      }

      const role = isRegister ? rRole : lRole;
      animateTextChange(() => {
        if (leftTitle && leftDesc) {
            if (isRegister) {
              leftTitle.innerHTML = "Start Your<br>Story.";
              leftDesc.textContent = role === "retailer"
                ? "Open your store and reach customers today."
                : "Discover amazing products from verified retailers.";
            } else {
              leftTitle.innerHTML = "Hello,<br>Welcome!";
              leftDesc.textContent = role === "retailer"
                ? "Sign in to manage your store and orders."
                : "Sign in to continue shopping your favourite items.";
            }
        }
        updatePill(role);
      });
      clearAuthMessage();
    }

    function setLoginRole(role) {
      lRole = role;
      const roleInput = byId("lRoleInput");
      const customerBtn = byId("lBtnCustomer");
      const retailerBtn = byId("lBtnRetailer");
      const loginBtn = byId("loginBtn");

      if (roleInput) {
          roleInput.value = role;
      }
      if (customerBtn) {
          customerBtn.className = "role-sel-btn" + (role === "customer" ? " sel-customer" : "");
      }
      if (retailerBtn) {
          retailerBtn.className = "role-sel-btn" + (role === "retailer" ? " sel-retailer" : "");
      }
      if (loginBtn) {
          loginBtn.className = "primary-btn" + (role === "retailer" ? " retailer-btn" : "");
      }
      if (!isRegister) {
          updatePill(role);
      }
    }

    function setRegRole(role) {
      rRole = role;
      const roleInput = byId("rRoleInput");
      const customerBtn = byId("rBtnCustomer");
      const retailerBtn = byId("rBtnRetailer");
      const registerBtn = byId("registerBtn");

      if (roleInput) {
          roleInput.value = role;
      }
      if (customerBtn) {
          customerBtn.className = "role-sel-btn" + (role === "customer" ? " sel-customer" : "");
      }
      if (retailerBtn) {
          retailerBtn.className = "role-sel-btn" + (role === "retailer" ? " sel-retailer" : "");
      }
      if (registerBtn) {
          registerBtn.className = "primary-btn" + (role === "retailer" ? " retailer-btn" : "");
      }
      if (isRegister) {
          updatePill(role);
      }
    }

    function togglePassword() {
      const input = byId("loginPass");
      const toggle = root.querySelector(".pass-toggle");
      if (!input || !toggle) {
          return;
      }
      if (input.type === "password") {
          input.type = "text";
          toggle.textContent = "HIDE";
      } else {
          input.type = "password";
          toggle.textContent = "SHOW";
      }
    }

        // Expose inline auth handlers immediately so auth buttons work
        // even before async bridge initialization completes.
        window.setLoginRole = setLoginRole;
        window.setRegRole = setRegRole;
        window.togglePassword = togglePassword;

    function showAuthView() {
        const auth = byId("rp-auth-view");
        const app = byId("rp-app-view");
        if (auth) {
            auth.classList.add("active");
        }
        if (app) {
            app.classList.remove("active");
        }
    }

    function showAppView() {
        const auth = byId("rp-auth-view");
        const app = byId("rp-app-view");
        if (auth) {
            auth.classList.remove("active");
        }
        if (app) {
            app.classList.add("active");
        }
    }

    function saveSessionToken(token) {
        state.sessionToken = asText(token || "");
        try {
            if (state.sessionToken) {
                localStorage.setItem(STORAGE_KEY, state.sessionToken);
            } else {
                localStorage.removeItem(STORAGE_KEY);
            }
        } catch (_err) {
            // ignore storage failures
        }
    }

    function readSessionToken() {
        try {
            return asText(localStorage.getItem(STORAGE_KEY) || "");
        } catch (_err) {
            return "";
        }
    }

    function resetLoaded() {
        Object.keys(state.loaded).forEach((k) => {
            state.loaded[k] = false;
        });
    }

    function updateSidebarUser(user) {
        const username = asText(user && user.username ? user.username : "Retailer");
        const email = asText(user && user.email ? user.email : "retailer@shop.com");
        const avatar = username.trim() ? username.trim().charAt(0).toUpperCase() : "R";
        const un = byId("rp-sidebar-user");
        const em = byId("rp-sidebar-email");
        const av = byId("rp-user-avatar");
        if (un) {
            un.textContent = username;
        }
        if (em) {
            em.textContent = email;
        }
        if (av) {
            av.textContent = avatar;
        }
    }

    async function logout(showMsg) {
        const currentToken = asText(state.sessionToken);
        if (currentToken) {
            try {
                await callBridge("logout", { session_token: currentToken });
            } catch (_err) {
                // ignore logout bridge errors
            }
        }
        saveSessionToken("");
        state.user = null;
        state.pendingToken = "";
        resetLoaded();
        showAuthView();
        setMode(false);
        clearAuthMessage();
        if (showMsg) {
            toast("Logged out.", "ok");
        }
    }

    async function withRetailerSession(action, payload) {
        if (!state.sessionToken) {
            throw new Error("Please login first.");
        }
        const body = Object.assign({}, payload || {}, { session_token: state.sessionToken });
        try {
            return await callBridge(action, body);
        } catch (err) {
            const msg = asText(err && err.message ? err.message : err);
            if (/session/i.test(msg)) {
                await logout(false);
                throw new Error("Session expired. Please login again.");
            }
            throw err;
        }
    }

    function setView(viewName) {
        state.currentView = viewName;
        const navs = root.querySelectorAll(".rp-nav-btn[data-nav]");
        navs.forEach((btn) => {
            btn.classList.toggle("active", btn.getAttribute("data-nav") === viewName);
        });

        const views = root.querySelectorAll(".rp-view[data-view]");
        views.forEach((v) => {
            v.classList.toggle("active", v.getAttribute("data-view") === viewName);
        });

        const title = byId("rp-view-title");
        if (title) {
            title.textContent = VIEW_TITLES[viewName] || "Retailer";
        }
    }

    function emptyRow(colspan, text) {
        return '<tr><td colspan="' + colspan + '">' + escapeHtml(text) + "</td></tr>";
    }

    function renderDashboard(data) {
        const stats = (data && data.stats) || {};
        byId("rp-stat-products").textContent = asText(stats.product_count || 0);
        byId("rp-stat-orders").textContent = asText(stats.order_count || 0);
        byId("rp-stat-revenue").textContent = fmtMoney(stats.revenue || 0);
        byId("rp-stat-pending").textContent = asText(stats.pending_count || 0);

        const recentBody = byId("rp-dashboard-orders-body");
        const topBody = byId("rp-dashboard-top-body");
        const recent = (data && data.recent_orders) || [];
        const top = (data && data.top_products) || [];

        if (recentBody) {
            if (!recent.length) {
                recentBody.innerHTML = emptyRow(6, "No retailer orders yet.");
            } else {
                recentBody.innerHTML = recent
                    .map((row) =>
                        '<tr>' +
                        '<td>#' + escapeHtml(row.id) + '</td>' +
                        '<td>' + escapeHtml(row.shipping_name || "-") + '</td>' +
                        '<td>' + escapeHtml(row.status || "-") + '</td>' +
                        '<td>' + escapeHtml(row.items_summary || "-") + '</td>' +
                        '<td>' + escapeHtml(fmtMoney(row.total_amount || 0)) + '</td>' +
                        '<td>' + escapeHtml(asText(row.created_at || "").slice(0, 16)) + '</td>' +
                        '</tr>'
                    )
                    .join("");
            }
        }

        if (topBody) {
            if (!top.length) {
                topBody.innerHTML = emptyRow(4, "No product performance data yet.");
            } else {
                topBody.innerHTML = top
                    .map((row) =>
                        '<tr>' +
                        '<td>' + escapeHtml(row.name || "-") + '</td>' +
                        '<td>' + escapeHtml(row.stock == null ? "-" : row.stock) + '</td>' +
                        '<td>' + escapeHtml(fmtMoney(row.price || 0)) + '</td>' +
                        '<td>' + escapeHtml(row.sold || 0) + '</td>' +
                        '</tr>'
                    )
                    .join("");
            }
        }
    }

    function buildCategoryOptions(categories) {
        const base = '<option value="">No Category</option>';
        const extra = (categories || [])
            .map((c) => '<option value="' + escapeHtml(c.id) + '">' + escapeHtml(c.name) + '</option>')
            .join("");
        return base + extra;
    }

    function renderProducts() {
        const body = byId("rp-products-body");
        const categorySelect = byId("rp-product-category");
        if (categorySelect) {
            categorySelect.innerHTML = buildCategoryOptions(state.categories);
        }

        if (!body) {
            return;
        }

        if (!state.products.length) {
            body.innerHTML = emptyRow(7, "No products found for this retailer.");
            return;
        }

        body.innerHTML = state.products
            .map((p) => {
                const status = Number(p.is_active || 0) === 1 ? "Active" : "Inactive";
                return (
                    '<tr>' +
                    '<td>#' + escapeHtml(p.id) + '</td>' +
                    '<td>' + escapeHtml(p.name || "") + '</td>' +
                    '<td>' + escapeHtml(p.category_name || "-") + '</td>' +
                    '<td>' + escapeHtml(fmtMoney(p.price || 0)) + '</td>' +
                    '<td>' + escapeHtml(p.stock || 0) + '</td>' +
                    '<td>' + escapeHtml(status) + '</td>' +
                    '<td><button class="rp-btn-ghost" data-edit-product="' + escapeHtml(p.id) + '">Edit</button></td>' +
                    '</tr>'
                );
            })
            .join("");
    }

    function fillProductForm(product) {
        byId("rp-product-id").value = product ? asText(product.id || "") : "";
        byId("rp-product-name").value = product ? asText(product.name || "") : "";
        byId("rp-product-description").value = product ? asText(product.description || "") : "";
        byId("rp-product-price").value = product ? asText(product.price || "") : "";
        byId("rp-product-original-price").value = product ? asText(product.original_price || "") : "";
        byId("rp-product-stock").value = product ? asText(product.stock || "") : "";
        byId("rp-product-category").value = product && product.category_id ? asText(product.category_id) : "";
        byId("rp-product-sku").value = product ? asText(product.sku || "") : "";
        byId("rp-product-weight").value = product ? asText(product.weight_grams || "") : "";
        byId("rp-product-image").value = product ? asText(product.image_url || "") : "";
        byId("rp-product-active").value = product ? (Number(product.is_active || 0) === 1 ? "1" : "0") : "1";
    }

    function readProductForm() {
        return {
            product_id: asText(byId("rp-product-id").value || ""),
            name: asText(byId("rp-product-name").value || "").trim(),
            description: asText(byId("rp-product-description").value || ""),
            price: asText(byId("rp-product-price").value || "").trim(),
            original_price: asText(byId("rp-product-original-price").value || "").trim(),
            stock: asText(byId("rp-product-stock").value || "").trim(),
            category_id: asText(byId("rp-product-category").value || "").trim(),
            sku: asText(byId("rp-product-sku").value || ""),
            weight_grams: asText(byId("rp-product-weight").value || "").trim(),
            image_url: asText(byId("rp-product-image").value || ""),
            is_active: asText(byId("rp-product-active").value || "1"),
        };
    }

    function renderOrders() {
        const body = byId("rp-orders-body");
        if (!body) {
            return;
        }

        if (!state.orders.length) {
            body.innerHTML = emptyRow(6, "No orders found for current filter.");
            return;
        }

        body.innerHTML = state.orders
            .map((o) => {
                const options = STATUS_OPTIONS
                    .map((st) => {
                        const sel = st === asText(o.status || "") ? " selected" : "";
                        return '<option value="' + st + '"' + sel + '>' + st + '</option>';
                    })
                    .join("");

                return (
                    '<tr>' +
                    '<td>#' + escapeHtml(o.id) + '<br><small>' + escapeHtml(asText(o.created_at || "").slice(0, 16)) + '</small></td>' +
                    '<td>' + escapeHtml(o.shipping_name || "-") + '<br><small>' + escapeHtml(o.shipping_phone || "") + '</small></td>' +
                    '<td>' + escapeHtml(o.items_summary || "-") + '</td>' +
                    '<td>' + escapeHtml(fmtMoney(o.retailer_total || 0)) + '</td>' +
                    '<td><select class="rp-select" data-order-select="' + escapeHtml(o.id) + '">' + options + '</select></td>' +
                    '<td><button class="rp-btn-ghost" data-save-order="' + escapeHtml(o.id) + '">Save</button></td>' +
                    '</tr>'
                );
            })
            .join("");
    }

    function renderBotPayload(payload) {
        if (!payload || typeof payload !== "object") {
            return escapeHtml(asText(payload || ""));
        }
        const answer = asText(payload.answer || payload.reply || payload.error || "");
        const query = asText(payload.query_ran || "");
        const dbOutput = asText(payload.db_output || "");
        const status = asText(payload.status || "");
        let html = '<div class="rp-msg-section"><div>' + escapeHtml(answer) + '</div></div>';

        if (query) {
            html += '<div class="rp-msg-section"><div class="rp-msg-title">Query</div><div class="rp-msg-pre">' + escapeHtml(query) + '</div></div>';
        }
        if (dbOutput) {
            html += '<div class="rp-msg-section"><div class="rp-msg-title">DB Output</div><div class="rp-msg-pre">' + escapeHtml(dbOutput) + '</div></div>';
        }
        if (status) {
            html += '<div class="rp-msg-section"><div class="rp-msg-title">Status</div><div class="rp-msg-pre">' + escapeHtml(status) + '</div></div>';
        }
        return html;
    }

    function getChatNode() {
        return byId("rp-assistant-messages");
    }

    function appendChatMessage(role, message, createdAt) {
        const chat = getChatNode();
        if (!chat) {
            return;
        }
        const bubble = document.createElement("div");
        bubble.className = "rp-msg " + (role === "user" ? "user" : "bot");

        if (role === "bot") {
            bubble.innerHTML = renderBotPayload(message);
        } else {
            bubble.textContent = asText(message || "");
        }

        const meta = document.createElement("div");
        meta.className = "rp-msg-meta";
        meta.textContent = createdAt ? asText(createdAt) : role === "user" ? "You" : "Sage";
        bubble.appendChild(meta);

        chat.appendChild(bubble);
        chat.scrollTop = chat.scrollHeight;
    }

    function appendTyping() {
        const chat = getChatNode();
        if (!chat) {
            return;
        }
        const bubble = document.createElement("div");
        bubble.className = "rp-msg bot";
        bubble.innerHTML = '<div class="rp-typing"><span></span><span></span><span></span></div>';
        chat.appendChild(bubble);
        chat.scrollTop = chat.scrollHeight;
        state.typingNode = bubble;
    }

    function removeTyping() {
        if (state.typingNode && state.typingNode.parentNode) {
            state.typingNode.parentNode.removeChild(state.typingNode);
        }
        state.typingNode = null;
    }

    async function loadDashboard(force) {
        if (!force && state.loaded.dashboard) {
            return;
        }
        setBusy(true, "Loading dashboard...");
        try {
            const data = await withRetailerSession("retailer_dashboard", {});
            renderDashboard(data);
            state.loaded.dashboard = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadProducts(force) {
        if (!force && state.loaded.products) {
            return;
        }
        setBusy(true, "Loading products...");
        try {
            const data = await withRetailerSession("retailer_products", {});
            state.products = data.products || [];
            state.categories = data.categories || [];
            renderProducts();
            state.loaded.products = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadOrders(force) {
        if (!force && state.loaded.orders) {
            return;
        }
        setBusy(true, "Loading orders...");
        try {
            const status = asText(byId("rp-orders-filter") && byId("rp-orders-filter").value).trim();
            const data = await withRetailerSession("retailer_orders", { status: status });
            state.orders = data.orders || [];
            renderOrders();
            state.loaded.orders = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadProfile(force) {
        if (!force && state.loaded.profile) {
            return;
        }
        setBusy(true, "Loading profile...");
        try {
            const data = await withRetailerSession("retailer_profile", {});
            const user = data.user || {};
            byId("rp-profile-name").value = asText(user.username || "");
            byId("rp-profile-email").value = asText(user.email || "");
            byId("rp-profile-phone").value = asText(user.phone_number || "");
            byId("rp-profile-bio").value = asText(user.bio || "");
            state.loaded.profile = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadAssistantHistory(force) {
        if (!force && state.loaded.assistant) {
            return;
        }
        setBusy(true, "Loading assistant chat...");
        try {
            const data = await withRetailerSession("assistant_history", {});
            const history = data.history || [];
            const chat = getChatNode();
            if (chat) {
                chat.innerHTML = "";
            }
            if (!history.length) {
                appendChatMessage("bot", "Hello, I am Sage. Ask me about products, stock, orders, or growth.", "");
            } else {
                history.forEach((item) => {
                    appendChatMessage(item.sender || "bot", item.message || "", item.created_at || "");
                });
            }
            state.loaded.assistant = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadCurrentView(force) {
        if (state.currentView === "dashboard") {
            await loadDashboard(force);
            return;
        }
        if (state.currentView === "products") {
            await loadProducts(force);
            return;
        }
        if (state.currentView === "orders") {
            await loadOrders(force);
            return;
        }
        if (state.currentView === "assistant") {
            await loadAssistantHistory(force);
            return;
        }
        if (state.currentView === "profile") {
            await loadProfile(force);
        }
    }

    async function switchView(viewName, force) {
        setView(viewName);
        try {
            await loadCurrentView(Boolean(force));
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        }
    }

    async function saveProduct() {
        const form = readProductForm();
        if (!form.name) {
            toast("Product name is required.", "err");
            return;
        }

        setBusy(true, "Saving product...");
        try {
            if (form.product_id) {
                await withRetailerSession("retailer_edit_product", form);
                toast("Product updated.", "ok");
            } else {
                await withRetailerSession("retailer_add_product", form);
                toast("Product added.", "ok");
            }
            fillProductForm(null);
            state.loaded.products = false;
            state.loaded.dashboard = false;
            await loadProducts(true);
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        } finally {
            setBusy(false);
        }
    }

    async function deactivateCurrentProduct() {
        const id = asText(byId("rp-product-id").value || "").trim();
        if (!id) {
            toast("Select a product first.", "err");
            return;
        }

        setBusy(true, "Deactivating product...");
        try {
            await withRetailerSession("retailer_delete_product", { product_id: id });
            toast("Product deactivated.", "ok");
            fillProductForm(null);
            state.loaded.products = false;
            state.loaded.dashboard = false;
            await loadProducts(true);
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        } finally {
            setBusy(false);
        }
    }

    async function saveOrderStatus(orderId) {
        const sel = root.querySelector('[data-order-select="' + orderId + '"]');
        if (!sel) {
            toast("Order status selector missing.", "err");
            return;
        }
        setBusy(true, "Updating order status...");
        try {
            await withRetailerSession("retailer_update_order_status", {
                order_id: orderId,
                status: asText(sel.value || ""),
            });
            toast("Order status updated.", "ok");
            state.loaded.orders = false;
            state.loaded.dashboard = false;
            await loadOrders(true);
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        } finally {
            setBusy(false);
        }
    }

    async function saveProfile() {
        setBusy(true, "Updating profile...");
        try {
            const payload = {
                username: asText(byId("rp-profile-name").value || "").trim(),
                phone_number: asText(byId("rp-profile-phone").value || "").trim(),
                bio: asText(byId("rp-profile-bio").value || "").trim(),
            };
            await withRetailerSession("retailer_profile_update", payload);
            if (state.user) {
                state.user.username = payload.username || state.user.username;
                updateSidebarUser(state.user);
            }
            toast("Profile updated.", "ok");
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        } finally {
            setBusy(false);
        }
    }

    async function sendAssistantText(messageText) {
        const text = asText(messageText || "").trim();
        if (!text) {
            return;
        }
        appendChatMessage("user", text, "");
        appendTyping();
        try {
            const data = await withRetailerSession("assistant_chat", { message: text });
            removeTyping();
            appendChatMessage("bot", data.reply || "No response.", "");
        } catch (err) {
            removeTyping();
            appendChatMessage("bot", { answer: asText(err && err.message ? err.message : err) }, "");
        }
    }

    async function handleLoginSubmit(event) {
        event.preventDefault();
        clearAuthMessage();
        setBusy(true, "Logging in...");

        try {
            const form = getAuthForm("login");
            if (!form) {
                throw new Error("Login form not found.");
            }
            const emailField = form.querySelector('input[name="email"]');
            const passwordField = form.querySelector('input[name="password"]');
            const email = asText(emailField && emailField.value).trim();
            const password = asText(passwordField && passwordField.value);
            const role = asText(byId("lRoleInput") && byId("lRoleInput").value).trim().toLowerCase() || "customer";

            const data = await callBridge("login", {
                email: email,
                password: password,
                role: role,
            });

            if (asText(data.user && data.user.role).toLowerCase() !== "retailer") {
                saveSessionToken("");
                state.user = null;
                setAuthMessage("Customer login is not available in this retailer portal. Please choose Retailer.", true, "login");
                return;
            }

            state.user = data.user || null;
            saveSessionToken(data.session_token || "");
            updateSidebarUser(state.user || {});
            showAppView();
            setView("dashboard");
            resetLoaded();
            await loadCurrentView(true);
            toast("Welcome back.", "ok");
        } catch (err) {
            setAuthMessage(asText(err && err.message ? err.message : err), true, "login");
        } finally {
            setBusy(false);
        }
    }

    async function handleRegisterSubmit(event) {
        event.preventDefault();
        clearAuthMessage();
        setBusy(true, "Creating account...");

        try {
            const form = getAuthForm("register");
            if (!form) {
                throw new Error("Register form not found.");
            }
            const usernameField = form.querySelector('input[name="username"]');
            const emailField = form.querySelector('input[name="email"]');
            const passwordField = form.querySelector('input[name="password"]');
            const role = asText(byId("rRoleInput") && byId("rRoleInput").value).trim().toLowerCase() || "customer";

            const payload = {
                username: asText(usernameField && usernameField.value).trim(),
                phone: "",
                email: asText(emailField && emailField.value).trim(),
                password: asText(passwordField && passwordField.value),
                role: role,
            };

            const registration = await callBridge("register", payload);
            state.pendingToken = asText(registration.pending_token || "");

            const verification = await callBridge("verify_otp", {
                pending_token: state.pendingToken,
                otp: asText(registration.otp_hint || ""),
            });

            if (asText(verification.user && verification.user.role).toLowerCase() !== "retailer") {
                saveSessionToken("");
                state.user = null;
                setMode(false);
                setAuthMessage("Customer account created. Please choose Retailer to access this portal.", true, "login");
                toast("Account created successfully.", "ok");
                return;
            }

            state.user = verification.user || null;
            saveSessionToken(verification.session_token || "");
            updateSidebarUser(state.user || {});
            showAppView();
            setView("dashboard");
            resetLoaded();
            await loadCurrentView(true);
            toast("Account verified successfully.", "ok");
        } catch (err) {
            setAuthMessage(asText(err && err.message ? err.message : err), true, "register");
        } finally {
            setBusy(false);
        }
    }

    async function trySessionResume() {
        const token = readSessionToken();
        if (!token) {
            showAuthView();
            return;
        }

        saveSessionToken(token);
        setBusy(true, "Restoring session...");
        try {
            const data = await callBridge("session_resume", { session_token: token });
            if (asText(data.user && data.user.role).toLowerCase() !== "retailer") {
                saveSessionToken("");
                state.user = null;
                showAuthView();
                return;
            }
            state.user = data.user || null;
            updateSidebarUser(state.user || {});
            showAppView();
            setView("dashboard");
            resetLoaded();
            await loadCurrentView(true);
        } catch (_err) {
            saveSessionToken("");
            showAuthView();
        } finally {
            setBusy(false);
        }
    }

    function bindEvents() {
        const tabLogin = byId("tabLogin");
        const tabRegister = byId("tabRegister");
        const toggleBtn = byId("toggleBtn");
        const loginForm = getAuthForm("login");
        const registerForm = getAuthForm("register");

        if (tabLogin) {
            tabLogin.onclick = () => setMode(false);
        }
        if (tabRegister) {
            tabRegister.onclick = () => setMode(true);
        }
        if (toggleBtn) {
            toggleBtn.onclick = () => {
                window.location.href = "/landing";
            };
        }

        const lBtnCustomer = byId("lBtnCustomer");
        if (lBtnCustomer) lBtnCustomer.onclick = () => setLoginRole("customer");
        const lBtnRetailer = byId("lBtnRetailer");
        if (lBtnRetailer) lBtnRetailer.onclick = () => setLoginRole("retailer");
        
        const rBtnCustomer = byId("rBtnCustomer");
        if (rBtnCustomer) rBtnCustomer.onclick = () => setRegRole("customer");
        const rBtnRetailer = byId("rBtnRetailer");
        if (rBtnRetailer) rBtnRetailer.onclick = () => setRegRole("retailer");

        root.querySelectorAll(".pass-toggle").forEach(pt => {
            pt.onclick = () => togglePassword();
        });

        if (loginForm) {
            loginForm.addEventListener("submit", handleLoginSubmit);
        }
        if (registerForm) {
            registerForm.addEventListener("submit", handleRegisterSubmit);
        }

        const logoutBtn = byId("rp-logout-btn");
        if (logoutBtn) {
            logoutBtn.addEventListener("click", async () => {
                await logout(true);
            });
        }

        root.querySelectorAll(".rp-nav-btn[data-nav]").forEach((btn) => {
            btn.addEventListener("click", async () => {
                const target = btn.getAttribute("data-nav");
                await switchView(target, false);
            });
        });

        const refreshBtn = byId("rp-top-refresh");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", async () => {
                state.loaded[state.currentView] = false;
                await loadCurrentView(true);
                toast("View refreshed.", "ok");
            });
        }

        const productsBody = byId("rp-products-body");
        if (productsBody) {
            productsBody.addEventListener("click", (event) => {
                const btn = event.target.closest("[data-edit-product]");
                if (!btn) {
                    return;
                }
                const id = asText(btn.getAttribute("data-edit-product") || "");
                const product = state.products.find((p) => asText(p.id) === id);
                if (product) {
                    fillProductForm(product);
                    toast("Editing product #" + id, "ok");
                }
            });
        }

        const saveProductBtn = byId("rp-product-save");
        const clearProductBtn = byId("rp-product-clear");
        const deleteProductBtn = byId("rp-product-delete");

        if (saveProductBtn) {
            saveProductBtn.addEventListener("click", saveProduct);
        }
        if (clearProductBtn) {
            clearProductBtn.addEventListener("click", () => fillProductForm(null));
        }
        if (deleteProductBtn) {
            deleteProductBtn.addEventListener("click", deactivateCurrentProduct);
        }

        const ordersBody = byId("rp-orders-body");
        if (ordersBody) {
            ordersBody.addEventListener("click", async (event) => {
                const btn = event.target.closest("[data-save-order]");
                if (!btn) {
                    return;
                }
                const id = asText(btn.getAttribute("data-save-order") || "");
                await saveOrderStatus(id);
            });
        }

        const ordersRefresh = byId("rp-orders-refresh");
        const ordersFilter = byId("rp-orders-filter");
        if (ordersRefresh) {
            ordersRefresh.addEventListener("click", async () => {
                state.loaded.orders = false;
                await loadOrders(true);
            });
        }
        if (ordersFilter) {
            ordersFilter.addEventListener("change", async () => {
                state.loaded.orders = false;
                await loadOrders(true);
            });
        }

        const profileSave = byId("rp-profile-save");
        if (profileSave) {
            profileSave.addEventListener("click", saveProfile);
        }

        const assistantSend = byId("rp-assistant-send");
        const assistantInput = byId("rp-assistant-input");
        if (assistantSend && assistantInput) {
            assistantSend.addEventListener("click", async () => {
                const text = asText(assistantInput.value || "").trim();
                if (!text) {
                    return;
                }
                assistantInput.value = "";
                await sendAssistantText(text);
            });

            assistantInput.addEventListener("keydown", async (event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    const text = asText(assistantInput.value || "").trim();
                    if (!text) {
                        return;
                    }
                    assistantInput.value = "";
                    await sendAssistantText(text);
                }
            });
        }

        root.querySelectorAll(".rp-chip[data-prompt]").forEach((chip) => {
            chip.addEventListener("click", async () => {
                const prompt = asText(chip.getAttribute("data-prompt") || "").trim();
                if (!prompt) {
                    return;
                }
                setView("assistant");
                await loadAssistantHistory(false);
                await sendAssistantText(prompt);
            });
        });
    }

    async function bootstrap() {
        root = document.getElementById("rp-root");
        if (!root) {
            return;
        }
        if (root.dataset.bound === "1") {
            return;
        }
        root.dataset.bound = "1";

        bindEvents();
        setMode(false);
        setLoginRole("customer");
        setRegRole("customer");
        fillProductForm(null);

        try {
            await waitForBridge();
            await trySessionResume();
        } catch (err) {
            setAuthMessage(asText(err && err.message ? err.message : err), true, "login");
            showAuthView();
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bootstrap);
    } else {
        bootstrap();
    }

    if (!root) {
        let tries = 0;
        const waitForRoot = () => {
            if (document.getElementById("rp-root")) {
                bootstrap();
                return;
            }
            tries += 1;
            if (tries < 120) {
                setTimeout(waitForRoot, 50);
            }
        };
        waitForRoot();
    }
}
