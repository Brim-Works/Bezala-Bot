/* C65 — vy 3: före/efter vid ifyllnad (fill_bezala_drafts). */
(function () {
  "use strict";
  var BB = window.BB, el = BB.el;

  function vat(x) { return ((x && x.vat_lines) || [])[0] || {}; }
  function cond(x, k) { return x && x.conditional ? x.conditional[k] : null; }
  function amount(x) {
    if (!x) return null;
    if (x.original_amount === undefined || x.original_amount === null) return null;
    return BB.money(x.original_amount, x.original_amount_currency) +
      (x.billed_amount !== undefined && x.billed_amount !== null && x.billed_amount_currency !== x.original_amount_currency
        ? " (" + BB.money(x.billed_amount, x.billed_amount_currency) + ")" : "");
  }
  function rows(x) {
    var lines = (x && x.vat_lines) || [];
    if (!lines.length) return null;
    return lines.length + " rad(er)" + (vat(x).cost_center_ids && vat(x).cost_center_ids.length
      ? ", kostnadsställe " + vat(x).cost_center_ids.join(", ") : "");
  }

  // [etikett, värde-funktion, ord i "skipped" som betyder att Mikko/Bezala redan satt fältet]
  var FIELDS = [
    ["Beskrivning", function (x) { return x && x.description; }, "Beskrivning"],
    ["Konto", function (x) { return vat(x).expense_account_id; }, "Konto"],
    ["Moms", function (x) { return vat(x).vat_id; }, "Moms"],
    ["Belopp", amount, "Belopp"],
    ["Kostnadsrader", rows, "Momsrader"],
    ["Vehicle", function (x) { return cond(x, "vehicle"); }, "Vehicle"],
    ["km", function (x) { return cond(x, "km"); }, "km"],
  ];

  function same(a, b) { return JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b); }

  function mark(before, after, skipped, word, apply) {
    if (!same(before, after)) return { cls: "warn", text: apply ? "ändrat av boten" : "ändras" };
    var byMikko = (skipped || []).some(function (s) {
      return s.indexOf(word) === 0 && /redan|behålls|rörs inte/.test(s);
    });
    if (byMikko) return { cls: "ok", text: "ändrat av Mikko - rörs inte" };
    return { cls: "idle", text: "orört" };
  }

  function itemCard(it, apply) {
    var box = el("div", { class: "card", "data-tx": it.transaction_id });
    box.appendChild(el("h2", {}, "Utkast " + BB.txt(it.transaction_id) + " · " + BB.txt(it.vendor)));
    if (it.error) { box.appendChild(el("div", { class: "note bad" }, it.error)); return box; }
    var st = it.state;
    box.appendChild(el("div", { class: "note " + (st === "draft" ? "ok" : "bad"), "data-state": st || "" },
      st === "draft" ? "State: draft - boten får skriva till tomma fält"
                     : "State: " + BB.txt(st) + " - inte ett utkast, boten skriver inte"));
    var t = el("table", {}, [el("tr", {}, [el("th", {}, "Fält"), el("th", {}, "Före"), el("th", {}, "Efter"), el("th", {}, "")])]);
    FIELDS.forEach(function (f) {
      var b = f[1](it.before), a = f[1](it.after);
      var m = mark(b, a, it.skipped, f[2], apply);
      t.appendChild(el("tr", { "data-field": f[0] }, [el("td", {}, f[0]), el("td", {}, BB.txt(b)), el("td", {}, BB.txt(a)),
        el("td", {}, el("span", { class: "pill " + m.cls }, m.text))]));
    });
    box.appendChild(el("div", { class: "table-wrap" }, t));
    if (it.account || it.vat) box.appendChild(el("div", { class: "muted" }, "Förslag: konto " + BB.txt(it.account) + ", moms " + BB.txt(it.vat)));
    var sk = it.skipped || [];
    if (sk.length) {
      box.appendChild(el("h2", { style: "margin-top:8px" }, "Hoppas över"));
      var ul = el("ul", {});
      sk.forEach(function (s) { ul.appendChild(el("li", {}, s)); });
      box.appendChild(ul);
    }
    if (it.result || it.write_error || it.error_after) {
      box.appendChild(el("div", { class: "note " + (it.write_error ? "bad" : "ok") }, BB.txt(it.result || it.write_error || it.error_after)));
    }
    // Del 2: en knapp per utkast (en skrivning åt gången), bara efter torrkörning.
    if (!apply && st === "draft" && Object.keys(it.changes || {}).length) {
      var b2 = BB.writeButton("Fyll i detta utkast",
        "Fyll i utkast " + BB.txt(it.transaction_id) + " (" + BB.txt(it.vendor) + ") med värdena i kolumnen Efter? Bara tomma fält, inget skickas.",
        "fill_bezala_drafts", { transaction_ids: [String(it.transaction_id)], apply: true },
        function (res, out) {
          var r = (res && res.items && res.items[0]) || {};
          out.textContent = BB.txt(res && res.note) + (r.state_after ? " State efter: " + r.state_after : "");
          if (res && res.items && res.items[0]) box.parentNode.replaceChild(itemCard(res.items[0], true), box);
        }, true);
      if (b2) box.appendChild(el("div", { class: "btns" }, b2));
    }
    return box;
  }

  BB.render = function (d) {
    var root = BB.clear(BB.root());
    if (!d.items) { root.appendChild(el("pre", {}, JSON.stringify(d, null, 2))); return; }
    root.appendChild(el("h1", {}, (d.apply ? "Ifyllnad - resultat" : "Ifyllnad - torrkörning") + " (" + (d.count || 0) + " utkast)"));
    root.appendChild(el("div", { class: "note " + (d.apply ? "warn" : "ok") }, BB.txt(d.note)));
    var drafts = d.items.filter(function (it) { return it.error || it.state === "draft"; });
    var others = d.items.filter(function (it) { return !(it.error || it.state === "draft"); });
    if (!drafts.length) root.appendChild(el("div", { class: "muted" }, "Inga utkast att fylla i."));
    drafts.forEach(function (it) { root.appendChild(itemCard(it, !!d.apply)); });
    if (others.length) {
      // Inte utkast (t.ex. reviewing/accounted): boten skriver inte. Kort lista.
      var box = el("div", { class: "card" }, [
        el("h2", {}, "Hoppas över - inte utkast (" + others.length + ")"),
        el("div", { class: "muted" }, "Boten skriver bara till utkast (state draft). De här rörs inte."),
      ]);
      var t = el("table", {}, [el("tr", {}, [el("th", {}, "Utlägg"), el("th", {}, "Leverantör"),
        el("th", {}, "Belopp i Bezala"), el("th", {}, "State"), el("th", {}, "km-förslag")])]);
      others.forEach(function (it) {
        var bz = (it.coupling_before && it.coupling_before.bezala) || {};
        var dist = it.distance || {};
        t.appendChild(el("tr", { "data-tx": it.transaction_id }, [
          el("td", {}, BB.txt(it.transaction_id)), el("td", {}, BB.txt(it.vendor)),
          el("td", {}, BB.money(bz.original_amount, bz.original_amount_currency)),
          el("td", {}, el("span", { class: "pill bad", "data-state": it.state || "" }, BB.txt(it.state))),
          el("td", {}, dist.km !== null && dist.km !== undefined ? dist.km + " km " + BB.txt(dist.vehicle) +
            (dist.km_estimate ? " (uppskattat)" : "") : "km saknas"),
        ]));
      });
      box.appendChild(el("div", { class: "table-wrap" }, t));
      root.appendChild(box);
    }
  };
  BB.start("bezala-fill");
})();
