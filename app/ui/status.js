/* C65 — vy 2: statuskedja per utlägg (get_card_line, list_missing).
 * Kortrad → kvitto → Bezala-utkast → skickat (reviewing) → godkänt (accounted). */
(function () {
  "use strict";
  var BB = window.BB, el = BB.el;
  var CLS = { ok: "ok", bad: "bad", warn: "warn", idle: "idle" };

  // Steg ur get_card_line-svaret (eller en rad ur list_missing: d.card_line saknas då).
  function chainFor(d) {
    var line = d.card_line || null;
    var rec = (d.coupled_receipts || [])[0] || null;
    var sugg = d.suggested_receipt || null;
    var txs = (d.bezala_transaction && d.bezala_transaction.transactions) || [];
    var tx = txs.filter(function (t) { return !t.error; })[0] || null;
    var state = tx ? tx.state : null;
    var status = line ? line.status : null;
    var txId = (tx && tx.transaction_id) || (line && line.bezala_transaction_id) || (rec && rec.bezala_transaction_id);
    var steps = [];

    steps.push({ key: "card", label: "Kortrad", cls: line ? "ok" : "warn",
      text: line ? "Kortraden finns" : "Kortraden finns inte i botens lista",
      fields: line ? [["Leverantör", line.vendor || line.description], ["Belopp", BB.money(line.amount, line.currency)],
                      ["Datum", line.date], ["Status i boten", line.status], ["Id", line.bill_line_id]] : [] });

    var recCls = rec ? "ok" : sugg ? "warn" : status === "receipt_not_available" || status === "private_expense" ? "idle" : "bad";
    var r = rec || sugg;
    steps.push({ key: "receipt", label: "Kvitto", cls: recCls,
      text: rec ? "Kvitto kopplat" : sugg ? "Förslag finns, inte kopplat" :
            status === "receipt_not_available" ? "Inget kvitto finns (markerat)" :
            status === "private_expense" ? "Privat utlägg" : "Kvitto saknas",
      fields: r ? [["Leverantör", r.vendor], ["Belopp", BB.money(r.amount, r.currency)], ["Datum", r.receipt_date || r.date],
                   ["Poäng", r.score], ["Kvitto", BB.link(r.drive_link, r.file_name || "Öppna i Drive")]] : [],
      match: r ? { bill_line_id: (line && line.bill_line_id) || d.bill_line_id, message_id: r.message_id } : null });

    var draftCls = "idle", draftText = "Inget utkast ännu";
    if (status === "coupling_unverified") { draftCls = "bad"; draftText = "Utkast skapat, koppling inte verifierad än (coupling_unverified)"; }
    else if (d._fill_status === "fill_unverified") { draftCls = "bad"; draftText = "Ifyllnaden kunde inte verifieras (fill_unverified)"; }
    else if (state === "draft") { draftCls = "ok"; draftText = "Utkast i Bezala (state draft)"; }
    else if (state === "reviewing" || state === "accounted") {
      draftCls = "ok"; draftText = "Utkastet är skickat vidare (state " + state + ") - boten skriver inte längre"; }
    else if (state) { draftCls = "bad"; draftText = "Inte ett utkast (state " + state + ") - fastnat? Boten skriver inte"; }
    else if (txId) { draftCls = "warn"; draftText = "Utkast " + txId + " - state inte läst"; }
    steps.push({ key: "draft", label: "Bezala-utkast", cls: draftCls, text: draftText,
      fields: [["Utlägg", txId], ["State", state || "inte läst"], ["Koppling", line && line.coupling]] });

    var sent = state === "reviewing" || state === "accounted";
    steps.push({ key: "reviewing", label: "Skickat", cls: sent ? "ok" : "idle",
      text: sent ? "Skickat för attest (av Mikko i Bezala)" : "Inte skickat. Boten skickar aldrig - det gör Mikko i Bezala.",
      fields: [["State", state || "inte läst"], ["Attesterare", tx && tx.status_fields ? (tx.status_fields.approvers || []).join(", ") : null]] });
    steps.push({ key: "accounted", label: "Godkänt", cls: state === "accounted" ? "ok" : "idle",
      text: state === "accounted" ? "Bokfört (accounted)" : "Inte bokfört än",
      fields: [["State", state || "inte läst"]] });
    return steps;
  }
  BB.chainFor = chainFor;

  function chainView(d, open) {
    var steps = chainFor(d);
    var detail = el("div", { class: "detail" });
    var chain = el("div", { class: "chain", "data-view": "chain" });
    var buttons = [];
    function select(i) {
      buttons.forEach(function (b, j) { b.className = "step " + CLS[steps[j].cls] + (i === j ? " sel" : ""); });
      BB.clear(detail);
      var s = steps[i];
      detail.appendChild(el("div", { class: "note " + CLS[s.cls] }, s.text));
      if (s.fields.length) detail.appendChild(BB.kv(s.fields));
      if (s.match && s.match.message_id) {
        var slot = el("div", { class: "muted" }, "Hämtar matchningen…");
        detail.appendChild(slot);
        BB.callTool("show_match", s.match).then(function (m) {
          detail.replaceChild(BB.matchCard(m), slot);
        }, function (e) { slot.textContent = "Kunde inte hämta matchningen: " + e.message; });
      }
    }
    steps.forEach(function (s, i) {
      if (i) chain.appendChild(el("span", { class: "arrow" }, "→"));
      var b = el("button", { class: "step " + CLS[s.cls], type: "button", "data-step": s.key, "data-cls": s.cls,
                             title: s.text, onclick: function () { select(i); } },
                 (s.cls === "ok" ? "✓ " : s.cls === "bad" ? "✗ " : s.cls === "warn" ? "! " : "") + s.label);
      buttons.push(b);
      chain.appendChild(b);
    });
    var box = el("div", {}, [chain, detail]);
    if (open !== undefined) select(open);
    return box;
  }

  function renderOne(root, d) {
    var line = d.card_line || {};
    root.appendChild(el("h1", {}, "Utlägg " + BB.txt(line.vendor || line.description || d.bill_line_id) + " " +
                                  BB.money(line.amount, line.currency)));
    var box = el("div", { class: "card" });
    root.appendChild(box);
    function draw() {
      var firstBad = chainFor(d).findIndex(function (s) { return s.cls === "bad"; });
      BB.clear(box).appendChild(chainView(d, firstBad >= 0 ? firstBad : 0));
    }
    draw();
    // Ifyllnadens status (fill_unverified) finns på kvittot: läs den med show_match.
    var rec = (d.coupled_receipts || [])[0];
    if (rec && rec.message_id && d.card_line) {
      BB.callTool("show_match", { bill_line_id: d.card_line.bill_line_id, message_id: rec.message_id })
        .then(function (m) {
          var fs = m && m.receipt ? m.receipt.draft_fill_status : null;
          if (fs) { d._fill_status = fs; draw(); }
        }, function () {});
    }
  }

  function renderMissing(root, d) {
    var lines = d.card_lines_without_receipt || [];
    root.appendChild(el("h1", {}, "Utlägg som saknar något (" + lines.length + ")"));
    root.appendChild(el("div", { class: "muted" }, "Klicka på en rad för att läsa Bezala (bara läsning)."));
    lines.forEach(function (c) {
      var item = { bill_line_id: c.bill_line_id, card_line: c, suggested_receipt: c.suggested_receipt,
                   coupled_receipts: [] };
      var body = el("div", {});
      var card = el("div", { class: "card", "data-bill-line": c.bill_line_id }, [
        el("div", { class: "clickable", onclick: function () {
          BB.clear(body).appendChild(el("div", { class: "muted" }, "Läser Bezala…"));
          BB.callTool("get_card_line", { bill_line_id: c.bill_line_id }).then(function (full) {
            BB.clear(body).appendChild(chainView(full, 0));
          }, function (e) { BB.clear(body).appendChild(el("div", { class: "note bad" }, "Fel: " + e.message)); });
        } }, [el("b", {}, BB.txt(c.vendor || c.description)), " " + BB.money(c.amount, c.currency) + " · " + BB.txt(c.date) +
              (c.age_days !== null && c.age_days !== undefined ? " · " + c.age_days + " dagar" : "")]),
        body,
      ]);
      body.appendChild(chainView(item));
      root.appendChild(card);
    });
    var trips = d.trip_items_missing || [];
    if (trips.length) {
      root.appendChild(el("h2", {}, "Saknas på resor (" + trips.length + ")"));
      var t = el("table", {}, [el("tr", {}, [el("th", {}, "Resa"), el("th", {}, "Vad"), el("th", {}, "Notering")])]);
      trips.forEach(function (i) {
        t.appendChild(el("tr", {}, [el("td", {}, BB.txt(i.destination) + " " + BB.txt(i.trip_dates)),
                                    el("td", {}, BB.txt(i.kind) + " " + BB.txt(i.slot)), el("td", {}, BB.txt(i.note))]));
      });
      root.appendChild(el("div", { class: "table-wrap" }, t));
    }
  }

  BB.render = function (d) {
    var root = BB.clear(BB.root());
    if (d.card_lines_without_receipt !== undefined) renderMissing(root, d);
    else if (d.bill_line_id !== undefined) renderOne(root, d);
    else root.appendChild(el("pre", {}, JSON.stringify(d, null, 2)));
  };
  BB.start("bezala-status");
})();
