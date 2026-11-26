/* C65 — gemensam kod för Bezala Bots MCP Apps-vyer.
 *
 * - Pratar bara med värden (Claude) via postMessage (JSON-RPC 2.0).
 * - Inga nätanrop: data kommer från verktygsresultat och tools/call via värden.
 * - All text från kvitton/kortrader renderas med textContent, aldrig som HTML.
 * - Skrivande verktyg nås bara via BB.confirmWrite (bekräftelse i UI:t),
 *   bara när knapparna är påslagna och bara en skrivning åt gången.
 * - Det finns inget verktyg eller någon knapp för att skicka för attest.
 */
(function () {
  "use strict";
  var CONFIG = window.BB_CONFIG || {};
  var BB = (window.BB = window.BB || {});
  BB.config = CONFIG;

  // Läsverktyg som vyerna får anropa själva (bara läsning).
  var READ_TOOLS = ["get_card_line", "show_match", "list_missing",
                    "list_pending_decisions", "fill_bezala_drafts"];
  // Skrivande verktyg: bara via BB.confirmWrite.
  var WRITE_TOOLS = ["decide", "couple_receipt", "fill_bezala_drafts"];
  BB.READ_TOOLS = READ_TOOLS;
  BB.WRITE_TOOLS = WRITE_TOOLS;

  // ---------------------------------------------------------------- DOM
  // el("div", {class: "x"}, ["text", node]) — strängar blir textnoder.
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === undefined || v === null || v === false) return;
      if (k === "class") n.className = v;
      else if (k === "onclick") n.addEventListener("click", v);
      else if (k === "text") n.textContent = String(v);
      else n.setAttribute(k, String(v));
    });
    (Array.isArray(children) ? children : children === undefined ? [] : [children])
      .forEach(function (c) {
        if (c === null || c === undefined || c === false) return;
        n.appendChild(typeof c === "object" && c.nodeType ? c : document.createTextNode(String(c)));
      });
    return n;
  }
  BB.el = el;
  BB.clear = function (n) { while (n.firstChild) n.removeChild(n.firstChild); return n; };

  BB.money = function (amount, currency) {
    if (amount === null || amount === undefined || amount === "") return "–";
    var n = Number(amount);
    var s = isNaN(n) ? String(amount) : n.toFixed(2).replace(".", ",");
    return s + " " + (currency || "");
  };
  BB.txt = function (v) { return v === null || v === undefined || v === "" ? "–" : String(v); };

  // Länk som öppnas av värden (ui/open-link). Bara https.
  BB.link = function (url, label) {
    if (!url || !/^https:\/\//i.test(String(url))) return el("span", { class: "muted" }, "ingen länk");
    return el("button", {
      class: "link", type: "button",
      onclick: function () { BB.request("ui/open-link", { url: String(url) }).catch(function () {}); },
    }, label || "Öppna");
  };

  BB.kv = function (rows) {
    var box = el("div", { class: "kv" });
    rows.forEach(function (r) {
      box.appendChild(el("div", { class: "k" }, r[0]));
      box.appendChild(el("div", { class: "v" }, r[1] && r[1].nodeType ? r[1] : BB.txt(r[1])));
    });
    return box;
  };

  // ---------------------------------------------------------------- JSON-RPC
  var nextId = 1;
  var pending = {};
  function send(msg) { window.parent.postMessage(msg, "*"); }
  BB.request = function (method, params) {
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { resolve: resolve, reject: reject };
      send({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
    });
  };
  BB.notify = function (method, params) { send({ jsonrpc: "2.0", method: method, params: params || {} }); };

  // Verktygsresultat → data (structuredContent, annars JSON i texten).
  BB.dataOf = function (result) {
    if (!result) return null;
    var sc = result.structuredContent;
    if (sc && typeof sc === "object") {
      var keys = Object.keys(sc);
      if (keys.length === 1 && keys[0] === "result") return sc.result;
      return sc;
    }
    var content = result.content || [];
    for (var i = 0; i < content.length; i++) {
      if (content[i] && content[i].type === "text") {
        try { return JSON.parse(content[i].text); } catch (e) { return { text: content[i].text }; }
      }
    }
    return null;
  };

  function isWrite(name, args) {
    if (name === "fill_bezala_drafts") return !!(args && args.apply);
    return WRITE_TOOLS.indexOf(name) >= 0;
  }
  BB.isWrite = isWrite;

  function rawCall(name, args) {
    return BB.request("tools/call", { name: name, arguments: args || {} }).then(function (res) {
      if (res && res.isError) {
        var d = BB.dataOf(res);
        throw new Error((d && (d.text || d.error)) || "Verktyget svarade med fel");
      }
      return BB.dataOf(res);
    });
  }

  // Bara läsning. Skrivande verktyg nekas här.
  BB.callTool = function (name, args) {
    if (isWrite(name, args) || READ_TOOLS.indexOf(name) < 0) {
      return Promise.reject(new Error("Inte tillåtet från vyn: " + name));
    }
    return rawCall(name, args);
  };

  // ---------------------------------------------------------------- skrivning
  var writing = false;
  BB.isWriting = function () { return writing; };

  // Fråga Mikko i UI:t. Löser med true/false.
  BB.confirm = function (text) {
    return new Promise(function (resolve) {
      var back, done = function (v) { document.body.removeChild(back); resolve(v); };
      back = el("div", { class: "modal-back", role: "dialog", "aria-modal": "true" }, [
        el("div", { class: "modal" }, [
          el("p", {}, text),
          el("div", { class: "btns" }, [
            el("button", { class: "btn primary", type: "button", "data-confirm": "yes",
                           onclick: function () { done(true); } }, "Ja, gör det"),
            el("button", { class: "btn", type: "button", "data-confirm": "no",
                           onclick: function () { done(false); } }, "Avbryt"),
          ]),
        ]),
      ]);
      document.body.appendChild(back);
    });
  };

  // Enda vägen till ett skrivande verktyg: knappar på + bekräftelse + en åt gången.
  BB.confirmWrite = function (text, name, args) {
    if (!CONFIG.buttons) return Promise.reject(new Error("Knapparna är avstängda"));
    if (WRITE_TOOLS.indexOf(name) < 0) return Promise.reject(new Error("Inte tillåtet: " + name));
    if (writing) return Promise.reject(new Error("Vänta - en skrivning pågår redan"));
    return BB.confirm(text).then(function (ok) {
      if (!ok) return null;
      writing = true;
      return rawCall(name, args).then(function (r) { writing = false; return r; },
                                      function (e) { writing = false; throw e; });
    });
  };

  // Knapp som skriver (renderas bara när knapparna är påslagna).
  BB.writeButton = function (label, confirmText, name, args, onDone, primary) {
    if (!CONFIG.buttons) return null;
    var out = el("div", { class: "muted" });
    var b = el("button", { class: "btn" + (primary ? " primary" : ""), type: "button", "data-write": name });
    b.textContent = label;
    b.addEventListener("click", function () {
      b.disabled = true;
      BB.confirmWrite(confirmText, name, args).then(function (res) {
        b.disabled = false;
        if (res === null) return;
        onDone && onDone(res, out);
      }, function (err) { b.disabled = false; out.textContent = "Fel: " + err.message; });
    });
    return el("span", {}, [b, out]);
  };

  // ---------------------------------------------------------------- värd
  function applyContext(ctx) {
    if (!ctx) return;
    if (ctx.theme === "dark" || ctx.theme === "light") document.documentElement.setAttribute("data-theme", ctx.theme);
    if (ctx.toolInfo && ctx.toolInfo.tool && ctx.toolInfo.tool.name) BB.toolName = ctx.toolInfo.tool.name;
  }

  window.addEventListener("message", function (ev) {
    if (ev.source !== window.parent) return;
    var m = ev.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.method === undefined && m.id !== undefined) {
      var p = pending[m.id];
      if (!p) return;
      delete pending[m.id];
      if (m.error) p.reject(new Error(m.error.message || "fel")); else p.resolve(m.result);
      return;
    }
    switch (m.method) {
      case "ui/notifications/tool-input":
        BB.toolArgs = (m.params && m.params.arguments) || {};
        break;
      case "ui/notifications/tool-result":
        try { BB.show(BB.dataOf(m.params), BB.toolName, m.params); }
        catch (e) { BB.showError(e); }
        break;
      case "ui/notifications/tool-cancelled":
        BB.showError(new Error("Avbrutet"));
        break;
      case "ui/notifications/host-context-changed":
        applyContext(m.params);
        break;
      case "ui/resource-teardown":
        send({ jsonrpc: "2.0", id: m.id, result: {} });
        break;
      default:
        if (m.id !== undefined) send({ jsonrpc: "2.0", id: m.id, error: { code: -32601, message: "okänd metod" } });
    }
  });

  BB.root = function () { return document.getElementById("app"); };
  BB.showError = function (e) {
    var r = BB.clear(BB.root());
    r.appendChild(el("div", { class: "note bad" }, "Kunde inte visa vyn: " + (e && e.message ? e.message : e)));
  };
  // Vyn sätter BB.render(data, toolName).
  BB.show = function (data, toolName) {
    if (data === null || data === undefined) { BB.showError(new Error("inget data")); return; }
    BB.render(data, toolName);
  };

  BB.start = function (appName) {
    var r = BB.root();
    if (r && !r.firstChild) r.appendChild(el("div", { class: "muted" }, "Väntar på data…"));
    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        var h = Math.ceil(document.documentElement.scrollHeight);
        BB.notify("ui/notifications/size-changed", { width: Math.ceil(document.documentElement.scrollWidth), height: h });
      }).observe(document.body);
    }
    if (window.parent === window) return; // inte i en värd
    BB.request("ui/initialize", {
      protocolVersion: "2026-01-26",
      appInfo: { name: appName, version: "1.0.0" },
      appCapabilities: {},
      clientInfo: { name: appName, version: "1.0.0" },
      capabilities: {},
    }).then(function (res) {
      applyContext(res && res.hostContext);
      BB.notify("ui/notifications/initialized", {});
    }, function () { BB.notify("ui/notifications/initialized", {}); });
  };

  // ---------------------------------------------------------------- delad vy: matchning
  var LABEL = { amount: "Belopp", date: "Datum", vendor: "Leverantör", location: "Plats" };

  // Kort med kortrad och kvitto bredvid varandra (data = show_match-svaret).
  BB.matchCard = function (d) {
    var card = d.card_line || {};
    var rec = d.receipt;
    var box = el("div", { class: "card", "data-view": "match" });
    var sides = el("div", { class: "row" }, [
      el("div", { class: "col side" }, [
        el("h2", {}, "Kortrad"),
        BB.kv([["Leverantör", card.vendor], ["Belopp", BB.money(card.amount, card.currency)],
               ["Valuta", card.currency], ["Datum", card.date], ["Plats", card.place]]),
      ]),
      el("div", { class: "col side" }, [
        el("h2", {}, rec ? (d.source === "suggested" ? "Kvitto (förslag)" : "Kvitto") : "Kvitto"),
        rec ? BB.kv([["Leverantör", rec.vendor], ["Belopp", BB.money(rec.amount, rec.currency)],
                     ["Valuta", rec.currency], ["Datum", rec.date], ["Plats", rec.place],
                     ["Kvitto", BB.link(rec.drive_link, rec.file_name || "Öppna kvittot i Drive")]])
            : el("div", { class: "note bad" }, "Inget kvitto hittat"),
      ]),
    ]);
    box.appendChild(sides);
    if (rec && d.score && !d.score.parts && d.score.total !== undefined && d.score.total !== null) {
      box.appendChild(el("div", { class: "score" }, [el("b", {}, "Poäng " + d.score.total),
        el("span", { class: "muted" }, " (uppdelningen hämtas…)")]));
    }
    if (rec && d.score && d.score.parts) {
      var parts = d.score.parts || {};
      var max = d.max_points || {};
      var ok = d.fields_ok || {};
      var fields = el("div", { class: "fields" });
      ["amount", "date", "vendor", "location"].forEach(function (k) {
        var c = ok[k] === true ? "ok" : ok[k] === false ? "bad" : "idle";
        fields.appendChild(el("div", { class: "field " + c, "data-field": k, "data-cls": c }, [
          el("b", {}, (c === "ok" ? "✓ " : c === "bad" ? "✗ " : "? ") + LABEL[k] + (c === "idle" ? " (okänd)" : "")),
          parts[k] + " av " + (max[k] || "?") + " p",
        ]));
      });
      box.appendChild(fields);
      var extra = [];
      if (d.score.date_days_off !== null && d.score.date_days_off !== undefined) extra.push(d.score.date_days_off + " dagar mellan datumen");
      if (d.score.conversion) extra.push("omräknat " + BB.money(d.score.conversion.from_amount, d.score.conversion.from_currency) +
                                         " → " + BB.money(d.score.conversion.to_amount, d.score.conversion.to_currency));
      box.appendChild(el("div", { class: "score" }, [
        el("b", {}, "Poäng " + BB.txt(d.score.total)),
        " = belopp " + parts.amount + " + datum " + parts.date + " + leverantör " + parts.vendor + " + plats " + parts.location,
        extra.length ? el("div", { class: "muted" }, extra.join(" · ")) : null,
      ]));
    }
    (d.warnings || []).forEach(function (w) {
      box.appendChild(el("div", { class: "note " + (w.kind === "no_receipt" || w.kind === "duplicate" ? "bad" : "warn"),
                                  "data-warning": w.kind }, w.text));
    });
    return box;
  };
})();
