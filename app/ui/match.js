/* C65 — vy 1: matchningskort (list_pending_decisions, show_match). */
(function () {
  "use strict";
  var BB = window.BB, el = BB.el;

  function afterCouple(res, out, billLineId) {
    var text = (res && (res.result || res.status || res.message)) || "Utkast skapat, koppling inte verifierad än.";
    out.textContent = String(text) + " Kollar kopplingen igen om 80 s (bara läsning)…";
    if (!billLineId) return;
    setTimeout(function () {
      BB.callTool("get_card_line", { bill_line_id: billLineId }).then(function (d) {
        var st = d && d.card_line ? d.card_line.status : "okänd";
        out.textContent = "Status nu: " + (st === "resolved" ? "kopplat och verifierat" :
          st === "coupling_unverified" ? "utkast skapat, koppling inte verifierad än" : st);
      }, function (e) { out.textContent = "Kunde inte läsa status: " + e.message; });
    }, 80000);
  }

  function decisionButtons(item) {
    if (!BB.config.buttons) return null;
    var rec = item.receipt || {}, card = item.card_line || {};
    var what = item.kind === "receipt"
      ? "kvitto " + BB.txt(rec.vendor) + " " + BB.money(rec.amount, rec.currency) +
        " till kortrad " + BB.txt(card.vendor || card.description) + " " + BB.money(card.amount, card.currency)
      : BB.txt(item.title);
    var yesText = item.kind === "receipt"
      ? "Koppla " + what + "? Utkast skapas, inget skickas."
      : BB.txt(item.yes_label) + ": " + what + "? Inget skickas.";
    var row = el("div", { class: "btns" });
    [
      BB.writeButton(item.yes_label || "Ja", yesText, "decide", { item_id: item.id, decision: "yes" },
        function (res, out) {
          if (item.kind === "receipt") afterCouple(res, out, card.bill_line_id);
          else out.textContent = "Klart: " + JSON.stringify(res);
        }, true),
      BB.writeButton(item.no_label || "Nej", BB.txt(item.no_label) + ": " + what + "?", "decide",
        { item_id: item.id, decision: "no" }, function (res, out) { out.textContent = "Klart."; }),
      BB.writeButton("Senare", "Hoppa över " + what + " just nu?", "decide",
        { item_id: item.id, decision: "later" }, function (res, out) { out.textContent = "Sparat till senare."; }),
    ].forEach(function (b) { if (b) row.appendChild(b); });
    return row;
  }

  function coupleButton(d) {
    if (!BB.config.buttons || !d.receipt || d.source !== "suggested") return null;
    var c = d.card_line || {}, r = d.receipt;
    if (c.status !== "missing" || c.bezala_transaction_id) return null;
    if ((d.warnings || []).length) return null; // dubblett, delat kvitto, saknat belopp …
    return el("div", { class: "btns" }, BB.writeButton(
      "Koppla kvitto",
      "Koppla kvitto " + BB.txt(r.vendor) + " " + BB.money(r.amount, r.currency) + " till kortrad " +
        BB.txt(c.vendor) + " " + BB.money(c.amount, c.currency) + "? Utkast skapas, inget skickas.",
      "couple_receipt", { bill_line_id: c.bill_line_id, message_id: r.message_id },
      function (res, out) { afterCouple(res, out, c.bill_line_id); }, true));
  }

  function renderSingle(root, d) {
    root.appendChild(el("h1", {}, "Matchning"));
    var card = BB.matchCard(d);
    var b = coupleButton(d);
    if (b) card.appendChild(b);
    root.appendChild(card);
  }

  function renderQueue(root, d) {
    var items = d.items || [];
    var receipts = items.filter(function (i) { return i.kind === "receipt"; });
    root.appendChild(el("h1", {}, "Beslut som väntar (" + BB.txt(d.total) + ")"));
    if (!receipts.length) root.appendChild(el("div", { class: "muted" }, "Inga kvitto↔kortrad-förslag just nu."));
    var queue = [];
    receipts.forEach(function (item) {
      var slot = el("div", {});
      // Snabbvy från kön; detaljer (poäng per fält) hämtas med show_match.
      slot.appendChild(BB.matchCard({
        card_line: item.card_line, receipt: Object.assign({ drive_link: item.receipt && item.receipt.drive_link }, item.receipt),
        source: "suggested", score: { total: item.score },
        warnings: item.warning ? [{ kind: "queue", text: item.warning }] : [],
      }));
      var btns = decisionButtons(item);
      if (btns) slot.appendChild(btns);
      root.appendChild(slot);
      queue.push(function () {
        return BB.callTool("show_match", { bill_line_id: item.card_line.bill_line_id,
                                            message_id: item.receipt.message_id })
          .then(function (det) {
            var has = (det.warnings || []).some(function (w) { return w.kind === "shared_refund"; });
            if (item.warning && !(has && item.shared_with_refund))
              det.warnings = (det.warnings || []).concat([{ kind: "queue", text: item.warning }]);
            slot.replaceChild(BB.matchCard(det), slot.firstChild);
          }, function () {});
      });
    });
    var others = items.filter(function (i) { return i.kind !== "receipt"; });
    if (others.length) {
      root.appendChild(el("h2", {}, "Andra beslut"));
      others.forEach(function (i) {
        var c = el("div", { class: "card" }, [el("b", {}, BB.txt(i.title)), el("div", { class: "muted" }, BB.txt(i.subtitle)),
          i.warning ? el("div", { class: "note warn" }, i.warning) : null]);
        var btns = decisionButtons(i);
        if (btns) c.appendChild(btns);
        root.appendChild(c);
      });
    }
    // En läsning åt gången (inte parallellt).
    queue.slice(0, 10).reduce(function (p, f) { return p.then(f); }, Promise.resolve());
  }

  BB.render = function (d, toolName) {
    var root = BB.clear(BB.root());
    if (d.items && d.counts !== undefined) renderQueue(root, d);
    else if (d.card_line !== undefined && d.max_points) renderSingle(root, d);
    else root.appendChild(el("pre", {}, JSON.stringify(d, null, 2)));
  };
  BB.start("bezala-match");
})();
