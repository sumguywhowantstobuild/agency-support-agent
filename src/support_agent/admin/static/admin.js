// Loaded in <head>, before the styles paint: the theme (dark or light) and the side menu (open or closed) come from
// what this browser kept, so the first paint is already right. The page itself says dark and open, which
// is also what a page without this script keeps. localStorage holds those two words and nothing else.
(function () {
  var root = document.documentElement;
  // Each choice: where it is kept, the page attribute it sets, the first visit's word, and the only words accepted.
  var CHOICES = {
    theme: { key: "admin-theme", attribute: "data-theme", first: "dark", words: ["dark", "light"] },
    menu: { key: "admin-side-nav", attribute: "data-side-nav", first: "open", words: ["open", "closed"] },
  };

  // What was kept, if it is exactly one of the words; anything else (storage blocked, a value nobody wrote) is none.
  function kept(choice) {
    try {
      var word = window.localStorage.getItem(choice.key);
      return choice.words.indexOf(word) === -1 ? null : word;
    } catch (error) {
      return null;
    }
  }

  function set(choice, word) {
    root.setAttribute(choice.attribute, word);
  }

  function choose(choice, word) {
    set(choice, word); // the page changes whether or not the browser can keep the choice
    try {
      window.localStorage.setItem(choice.key, word);
    } catch (error) {
      // blocked or full: the choice lasts for this page only
    }
  }

  set(CHOICES.theme, kept(CHOICES.theme) || CHOICES.theme.first);
  set(CHOICES.menu, kept(CHOICES.menu) || CHOICES.menu.first);

  // The buttons for them are hidden in the page until here: without this script they could not work. Their words
  // and pictures follow the choice, and one handler on the document serves every button, whenever it came.
  function showChoices() {
    var light = root.getAttribute("data-theme") === "light";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      var words = light ? "Switch to dark" : "Switch to light";
      button.hidden = false;
      button.setAttribute("aria-label", words);
      button.setAttribute("title", words);
      button.querySelector("use").setAttribute("href", light ? "#i-moon" : "#i-sun");
    });
    var shut = root.getAttribute("data-side-nav") === "closed";
    document.querySelectorAll("[data-side-nav-toggle]").forEach(function (button) {
      var words = shut ? "Open the menu" : "Collapse the menu";
      button.hidden = false;
      button.setAttribute("aria-label", words);
      button.setAttribute("title", words);
      button.setAttribute("aria-expanded", shut ? "false" : "true");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    showChoices();
    document.addEventListener("click", function (event) {
      if (!event.target.closest) return;
      if (event.target.closest("[data-theme-toggle]")) {
        choose(CHOICES.theme, root.getAttribute("data-theme") === "light" ? "dark" : "light");
        showChoices();
      } else if (event.target.closest("[data-side-nav-toggle]")) {
        choose(CHOICES.menu, root.getAttribute("data-side-nav") === "closed" ? "open" : "closed");
        showChoices();
      }
    });
  });

  // WebKit's page dies when a table arrives in its self-measuring wrapper during a view transition, or while one runs
  // (seen in Playwright's WebKit 27.2; Safari itself untried). Without the feature the motion add-on takes its own
  // fallback: changes at once, numbers still roll. Chrome's and Edge's agent strings also say AppleWebKit.
  if (/AppleWebKit/.test(navigator.userAgent) && !/Chrome\/|Chromium\/|Edg\//.test(navigator.userAgent)) {
    document.startViewTransition = undefined;
  }
  // A phone goes without too, by the bar's width, and it follows when a phone turns. While one runs the page is pictures,
  // and on a real phone the bar flickered, though not in a desktop browser's phone view.
  var startGlide = document.startViewTransition;
  var phoneWidth = window.matchMedia("(max-width: 720px)");
  var followWidth = function () { document.startViewTransition = phoneWidth.matches ? undefined : startGlide; };
  followWidth();
  phoneWidth.addEventListener("change", followWidth);

  // Clients: a limit changed where it is shown (design system inline edit). Enter saves through
  // POST /clients/{key}/limit; the answer's numbers go back into the tile and a toast says what changed.
  document.addEventListener("DOMContentLoaded", function () {
    var toast = document.getElementById("toast");
    if (!toast) return;

    // The design system's motion add-on, an enhancement: without it the same calls make the same change at once.
    var motion = window.dsMotion || {
      update: function (fn) { fn(); return Promise.resolve(); },
      remove: function (el, done) { done(); },
      enter: function () {},
    };

    var undoButton = toast.querySelector(".ds-toast__action");
    var undo = null; // what Undo does for the message on show, or null

    var polite = document.querySelector(".ds-toast__region[role=status]");
    var urgent = document.querySelector(".ds-toast__region[role=alert]");
    var announcing = 0;

    // Screen readers hear text that changes inside a region already on the page: empty both, then write after a
    // short pause, so the same message twice is heard twice and no old one is left behind (design system toast).
    function announce(words, isError) {
      polite.textContent = urgent.textContent = "";
      clearTimeout(announcing);
      announcing = setTimeout(function () { (isError ? urgent : polite).textContent = words; }, 100);
    }

    // A toast with Undo stays until it is dismissed or used; one with nothing to act on goes by itself, but not
    // while the pointer or the focus is on it (WCAG 2.2.1).
    var SHOWN_FOR = 6000;
    var hiding = 0;
    var held = { pointer: false, focus: false };

    // The list's panel (an item's, or Add), when the page has one (Clients, Staff): a <dialog>, modal while it is open.
    var panel = document.getElementById("panel");

    function hideToast() {
      clearTimeout(hiding);
      if (toast.matches(":popover-open")) toast.hidePopover();
      polite.textContent = urgent.textContent = "";
      clearRoom();
    }

    function clearRoom() {
      document.documentElement.style.scrollPaddingBottom = "";
      document.querySelectorAll(".ds-sheet__body").forEach(function (body) {
        body.style.scrollPaddingBottom = "";
        body.style.paddingBottom = "";
      });
    }

    // While it shows, the page (or, with the panel open, the panel's scrolling body) keeps the toast's height, its gap
    // and room for a focus ring clear at the bottom, so a field reached with Tab scrolls out from under it (WCAG
    // 2.4.11). The panel's body also gets that much padding, so its last controls have room to scroll into.
    function makeRoom() {
      var gap = parseFloat(getComputedStyle(toast).bottom) || 0;
      var room = Math.ceil(toast.offsetHeight + gap + 4);
      clearRoom();
      var body = panel && panel.open ? panel.querySelector(".ds-sheet__body") : null;
      if (body) {
        body.style.scrollPaddingBottom = room + "px";
        body.style.paddingBottom = "calc(var(--ds-space-8) + " + room + "px)";
      } else {
        document.documentElement.style.scrollPaddingBottom = room + "px";
      }
      // The control already focused (the field just saved, say) scrolls clear now, not only the next one Tab reaches.
      var focused = document.activeElement;
      if (focused && focused !== document.body && !toast.contains(focused) &&
          focused.getBoundingClientRect().bottom > toast.getBoundingClientRect().top - 4) {
        focused.scrollIntoView({ block: "nearest" });
      }
    }

    // One Escape goes to the innermost owner: a field being edited, then the toast, then the panel. Each that takes it
    // calls preventDefault, which also stops the browser's own close of a modal dialog.
    toast.querySelector(".ds-toast__close").addEventListener("click", hideToast);
    document.addEventListener("keydown", function (event) { // a field being edited takes its Escape first
      if (event.key === "Escape" && toast.matches(":popover-open")) {
        event.preventDefault();
        hideToast();
      }
    });

    function startTimer() {
      clearTimeout(hiding);
      if (!undo && !held.pointer && !held.focus) hiding = setTimeout(hideToast, SHOWN_FOR);
    }

    toast.addEventListener("mouseenter", function () { held.pointer = true; clearTimeout(hiding); });
    toast.addEventListener("mouseleave", function () { held.pointer = false; startTimer(); });
    toast.addEventListener("focusin", function () { held.focus = true; clearTimeout(hiding); });
    toast.addEventListener("focusout", function () { held.focus = false; startTimer(); });

    function showToast(message, onUndo, isError) {
      undo = onUndo || null;
      undoButton.hidden = !undo;
      toast.querySelector(".ds-toast__text").textContent = message;
      if (!toast.matches(":popover-open")) toast.showPopover();
      makeRoom();
      announce(undo ? message.replace(/\.$/, "") + ", Undo available" : message, isError);
      startTimer();
    }

    undoButton.addEventListener("click", function () {
      if (undo) undo();
    });

    // After an add, a remove or a change in an item's panel (a client's or a staff member's): the server names the item by
    // its saved name, and for a change that can be reversed gives a form that undoes it, only for a request this app's own
    // pages made. Said once: the address loses ?added (?removed, ?done), so a reload or a bookmark doesn't say it again.
    if (toast.dataset.toast) {
      var undoForm = toast.dataset.undo && document.getElementById(toast.dataset.undo);
      showToast(toast.dataset.toast, undoForm ? function () { undoForm.requestSubmit(); } : null);
      history.replaceState(null, "", location.pathname);
    }

    // The bar's level, 0 to 1, as the fill's scaleX (design system card: the app writes it).
    function fill(bar, level) {
      bar.style.transform = "scaleX(" + Number(Math.min(level, 1).toFixed(3)) + ")";
    }

    function fillBars(root) {
      root.querySelectorAll(".ds-card__fill[data-level]").forEach(function (bar) {
        fill(bar, Number(bar.dataset.level));
      });
    }

    // The short line before each client's name on its card or row matches that client's line on the chart on show: the
    // chart carries the map (data-series, by client key), and this sets the lines from it after any swap of the summary
    // or the cards, so each of them can arrive without the other. Only the line's small picture changes: never a field.
    function syncKeys() {
      var chart = document.querySelector("#summary .ds-chart[data-series]");
      if (!chart) return;
      var map;
      try { map = JSON.parse(chart.dataset.series); } catch (error) { return; }
      document.querySelectorAll("#results [data-ds-key]").forEach(function (item) {
        var name = item.querySelector(".ds-card__name-button, .ds-table__name");
        if (!name) return;
        var key = item.dataset.dsKey.replace(/^client-/, "");
        var series = Object.prototype.hasOwnProperty.call(map, key) ? String(map[key]) : "";
        var mark = name.querySelector(".ds-chart__key");
        if (!/^([1-9]|other)$/.test(series)) { // no line today, or a word nobody wrote
          if (mark) mark.remove();
          return;
        }
        if (!mark) {
          var SVG = "http://www.w3.org/2000/svg";
          mark = document.createElementNS(SVG, "svg");
          mark.setAttribute("class", "ds-chart__key");
          mark.setAttribute("viewBox", "0 0 18 2");
          mark.setAttribute("aria-hidden", "true");
          mark.appendChild(document.createElementNS(SVG, "path")).setAttribute("d", "M0 1H18");
          name.prepend(mark);
        }
        mark.querySelector("path").setAttribute("class", "ds-chart__line ds-chart__line--" + series);
      });
    }

    // A bar with an id is the same bar after a swap: it starts where the old one stood, so the fill's transition runs.
    function carryBars(old, region) {
      region.querySelectorAll(".ds-card__fill[id]").forEach(function (bar) {
        var before = old.querySelector("#" + CSS.escape(bar.id));
        if (!before) return;
        bar.style.transform = before.style.transform;
        getComputedStyle(bar).transform; // read now: the browser's start value, before fill writes the new level
      });
    }

    // The two states a limit change can move between: tone, icon, words (STATES in clients.py).
    var STATES = { drafting: ["ok", "i-check", "Drafting"], at_limit: ["warn", "i-warn", "At limit"] };

    // Saves per client, by its key, not on the field: a sort or the view switch puts a new field on the page while a
    // save may be on its way, so an answer or an Undo finds the field when it runs. made: how many saves were made.
    // queue: the last one. saved: what the database holds as far as the page knows. sending: the last value sent and
    // not yet answered. answer, answered: the newest answer and its place among all answers. (edited, typed since the
    // last save was sent, stays on the field: typing belongs to the box it was typed in.)
    var saves = Object.create(null); // no inherited names: a client called "Constructor" has the key "constructor"
    var answers = 0;

    function savesOf(key) {
      return saves[key] || (saves[key] = { made: 0, queue: Promise.resolve() });
    }

    // Every limit field for a client: its card on the list, and the one in its panel.
    function fieldsOf(key) {
      return Array.from(document.querySelectorAll(".ds-inline-edit__input[data-client='" + CSS.escape(key) + "']"));
    }

    function current(field) {
      var mine = savesOf(field.dataset.client);
      return mine.sending || mine.saved;
    }

    function apply(key, answer, latest) {
      var mine = savesOf(key);
      mine.saved = String(answer.limit_after);
      mine.answer = answer;
      mine.answered = ++answers;
      fieldsOf(key).forEach(function (field) { show(field, answer, latest); });
    }

    function show(field, answer, latest) {
      // The box takes the answer only from the newest save and only if nothing was typed since it was sent.
      if (latest && !field.dataset.edited) field.value = String(answer.limit_after);
      var holder = field.closest("[data-state]");
      if (holder.dataset.state === "paused") return; // no drafts, no bar: only the limit changed
      var state = answer.used_this_hour >= answer.limit_after ? "at_limit" : "drafting";
      var look = STATES[state];
      holder.dataset.state = state;
      var note = holder.querySelector(".ds-card__count-note");
      if (note) note.textContent = "of " + answer.limit_after + " this hour";
      var used = String(answer.used_this_hour); // drafts may have come in since the page loaded
      var number = holder.querySelector(".ds-card__count-number");
      if (number) number.textContent = used;
      var use = holder.querySelector(".ds-card__use"); // the client sheet's usage block
      if (use) {
        use.querySelector(".ds-card__use-number").textContent = used;
        use.querySelector(".ds-card__use-of").textContent = "of " + answer.limit_after + " drafts this hour";
        use.classList.toggle("ds-card__use--warn", state === "at_limit");
      }
      fill(holder.querySelector(".ds-card__fill"), answer.used_this_hour / answer.limit_after);
      var warn = state === "at_limit";
      holder.classList.toggle("ds-card--warn", warn && holder.classList.contains("ds-card--tile"));
      holder.querySelector(".ds-card__meter").classList.remove("ds-card__meter--warn"); // as before the list lost its field
      var badge = holder.querySelector(".ds-status");
      badge.classList.remove("ds-status--ok", "ds-status--warn");
      badge.classList.add("ds-status--" + look[0]);
      badge.querySelector("use").setAttribute("href", "#" + look[1]);
      var label = badge.querySelector(".ds-status__label");
      label.textContent = look[2];
      if (label.title) label.title = look[2];
    }

    var OFFLINE_ERROR = "Couldn't reach the admin app, so the limit may not have changed. Reload the page to check.";

    // Resolves with the server's answer once it is in the tile; rejects with words for the toast.
    function send(key, limit, number) {
      var body = new URLSearchParams({ limit: limit });
      return fetch("/clients/" + encodeURIComponent(key) + "/limit", { method: "POST", body: body })
        .catch(function () { throw new Error(OFFLINE_ERROR); })
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (answer) {
            if (!response.ok) throw new Error(answer.error || OFFLINE_ERROR);
            apply(key, answer, number === savesOf(key).made);
            return answer;
          });
        });
    }

    // One save at a time per client, in the order made: answers can't come back out of order, so the page ends where
    // the database ends (35 then 40 shows 40). Rejects with words for the toast and whether the box may be reset.
    function submit(key, limit) {
      var mine = savesOf(key);
      var number = mine.made += 1;
      mine.sending = limit;
      fieldsOf(key).forEach(function (field) { delete field.dataset.edited; });
      var run = mine.queue.then(function () { return send(key, limit, number); });
      mine.queue = run.catch(function () {});
      return run.finally(function () {
        if (number === mine.made) delete mine.sending; // a later save keeps its own
      }).catch(function (error) {
        error.reset = number === mine.made; // a newer save keeps its own value
        throw error;
      });
    }

    // The boxes go back to what the database holds, each on its own: one with typing in it stays as typed. `typedIn`,
    // for a limit refused before it was sent, is the box just typed in: it goes back too.
    function refused(key, error, typedIn) {
      if (error.reset !== false) {
        fieldsOf(key).forEach(function (field) {
          if (field.dataset.edited && field !== typedIn) return;
          field.value = current(field);
          delete field.dataset.edited;
          mark(field, false);
        });
      }
      showToast(error.message, null, true);
    }

    var LIMIT_ERROR = "The limit must be a whole number from 1 to 200."; // the server's words (clients.py)

    function validLimit(typed) {
      return /^[0-9]{1,3}$/.test(typed) && Number(typed) >= 1 && Number(typed) <= 200;
    }

    // Invalid: aria-invalid, and the unit (the field's description) says the allowed range after an error icon.
    function mark(field, invalid) {
      var unit = document.getElementById(field.getAttribute("aria-describedby"));
      if (!invalid) {
        field.removeAttribute("aria-invalid");
        unit.textContent = unit.dataset.words;
        return;
      }
      field.setAttribute("aria-invalid", "true");
      var icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      icon.setAttribute("aria-hidden", "true");
      use.setAttribute("href", "#i-alert");
      icon.appendChild(use);
      unit.replaceChildren(icon, "1 to 200");
    }

    function save(field) {
      var key = field.dataset.client, name = field.dataset.name;
      var typed = field.value.trim();
      if (typed === current(field)) return; // nothing new: Enter then Tab saves once, 30 back to 30 not at all
      if (!validLimit(typed)) { // refused here: the old value comes back and the toast says why
        refused(key, new Error(LIMIT_ERROR), field);
        return;
      }
      submit(key, typed).then(function (answer) {
        refreshSummary();
        showToast(name + ": " + answer.limit_after + " drafts an hour.", function () {
          submit(key, String(answer.limit_before)).then(function (back) {
            refreshSummary();
            showToast(name + ": back to " + back.limit_after + " drafts an hour.");
          }, function (error) { refused(key, error); });
        });
      }, function (error) { refused(key, error); });
    }

    // The page after a saved limit: Needs you, the main panel (the chart too) and the latest change,
    // from the server, or, if that fails, as they were. The cards or rows are fetched too only where no field of theirs
    // can be waiting for typing: with the panel open (the page is dimmed and inert), or in the list (it has no fields);
    // never from a card's own save, which would replace the next field while it is being typed in.
    function refreshSummary() {
      if (!document.getElementById("summary")) return;
      var regions = ["summary", "change-line"];
      if (opened || document.querySelector("#results table")) regions.push("results", "clients-count");
      refreshRegions(listAddress, regions, { quiet: true });
    }

    // The fields under root, new on the page (at load, or swapped in by a refresh sent when `sentAt` answers were
    // in): a save still on its way, or one answered since the refresh was sent, is newer than what the page shows.
    function adopt(root, sentAt) {
      root.querySelectorAll(".ds-inline-edit__input[data-client]").forEach(function (field) {
        var unit = document.getElementById(field.getAttribute("aria-describedby"));
        unit.dataset.words = unit.textContent; // "an hour", on a card and in the panel
        var mine = savesOf(field.dataset.client);
        var answeredSince = mine.answer && mine.answered > sentAt;
        if (!mine.sending && !answeredSince) {
          mine.saved = field.value;
          return;
        }
        field.value = current(field);
        if (answeredSince) show(field, mine.answer, false);
      });
    }

    // Fields are found as events reach the page, so a field swapped in by a refresh works like the first one.
    function limitField(target) {
      return target instanceof Element && target.matches(".ds-inline-edit__input[data-client]") ? target : null;
    }
    document.addEventListener("input", function (event) {
      var field = limitField(event.target);
      if (!field) return;
      field.dataset.edited = "true";
      mark(field, !validLimit(field.value.trim()));
    });
    document.addEventListener("focusout", function (event) { // leaving the field saves, as Enter does
      var field = limitField(event.target);
      if (field) save(field);
    });
    document.addEventListener("keydown", function (event) { // capture: before the toast's own Escape
      var field = limitField(event.target);
      if (!field) return;
      if (event.key === "Enter") save(field);
      if (event.key === "Escape" && field.value !== current(field)) {
        event.preventDefault(); // one Escape, to the innermost owner: the field being edited
        event.stopPropagation();
        field.value = current(field);
        delete field.dataset.edited;
        mark(field, false);
      }
    }, true);

    // Clients: a new sort and the Cards / List switch replace #results in place, so picking a sort never changes the
    // page (WCAG 2.2 3.2.2) and focus stays on the control. Only this app's own /clients page is fetched; its answer
    // is parsed apart (DOMParser: its scripts never run) and the named regions, escaped by the server, are moved in.
    // Resolves true once swapped; any failure loads the address as a page instead (quiet: changes nothing).
    var refreshes = {}; // the newest refresh of each set of regions: an older answer is dropped
    function refreshRegions(url, ids, options) {
      var quiet = options && options.quiet;
      var name = ids.join(" ");
      var mine = refreshes[name] = (refreshes[name] || 0) + 1;
      var sentAt = answers;
      return fetch(url, { headers: { Accept: "text/html" } })
        .then(function (response) {
          if (!response.ok || response.redirected) throw new Error("refresh refused");
          return response.text();
        })
        .then(function (html) {
          if (mine !== refreshes[name]) return false;
          var fresh = new DOMParser().parseFromString(html, "text/html");
          var parts = ids.map(function (id) { return fresh.getElementById(id); });
          if (parts.indexOf(null) >= 0) throw new Error("no region");
          var swap = function () {
            parts.forEach(function (part, i) {
              var old = document.getElementById(ids[i]);
              var region = document.importNode(part, true);
              // The page already drew its chart in once: a new one in its place shows still, not drawn in again.
              region.querySelectorAll(".ds-chart").forEach(function (chart) { chart.classList.add("ds-chart--still"); });
              old.replaceWith(region);
              carryBars(old, region);
              fillBars(region);
              adopt(region, sentAt);
            });
            if (!quiet) { // a quiet refresh may answer for an older address: the choices made since stay
              follow(fresh);
              listAddress = url;
              // With a panel open the address bar is the panel's: the list's address waits in listAddress until it closes.
              if (!opened) history.replaceState(null, "", url);
            }
            if (ids.indexOf("summary") >= 0) readNeeds();
            if (ids.indexOf("summary") >= 0 || ids.indexOf("results") >= 0) syncKeys();
            // A part that grew above (the latest change appears with the first change) moves the focused field: it is
            // scrolled clear of a showing toast again.
            if (toast.matches(":popover-open")) makeRoom();
          };
          // The swap glides, morphs and rolls its numbers, or just happens. Behind a panel (open, or still closing)
          // it just happens: nothing behind a dimmed page needs to glide, and a view transition around the top layer
          // is one more overlap for a crash like WebKit's.
          // Asked to change at once, it just happens too (switching Cards and List); a new bar has its level
          // before the browser first styles it, so it arrives full.
          if (opened || closing || (options && options.atOnce)) { swap(); return true; }
          return motion.update(swap).then(function () { return true; });
        })
        .catch(function () {
          // A failure while a panel is open leaves the list as it was: loading its address as a page would throw the
          // panel away.
          if (!quiet && !opened) location.assign(url);
          return false;
        });
    }

    // Both forms' hidden fields and the view buttons take the new page's values, so the next search, sort or switch
    // carries every choice made in place.
    function follow(fresh) {
      [["#find input[type=hidden], form.ds-segmented input[type=hidden]", "value"],
       [".ds-segmented__item", "aria-pressed"]].forEach(function (pair) {
        var olds = document.querySelectorAll(pair[0]), news = fresh.querySelectorAll(pair[0]);
        olds.forEach(function (old, i) {
          if (!news[i]) return;
          if (pair[1] === "value") old.value = news[i].value;
          else old.setAttribute(pair[1], news[i].getAttribute(pair[1]));
        });
      });
    }

    var find = document.getElementById("find");
    var sort = document.getElementById("sort");
    var views = document.querySelector("form.ds-segmented");
    if (find && sort && views && document.getElementById("results")) {
      // A phone has no glide: sorted results rise in instead, our own motion on the real page, which leaves the bar alone.
      var riseIn = function () {
        if (!phoneWidth.matches) return;
        var results = document.getElementById("results");
        motion.enter(results.querySelector("[data-ds-stagger]") || results);
      };
      document.querySelectorAll("[data-sort-button]").forEach(function (button) { button.hidden = true; });
      sort.addEventListener("change", function () {
        refreshRegions("/clients?" + new URLSearchParams(new FormData(find)), ["results"]).then(function (swapped) {
          if (!swapped) return;
          announce("Sorted by " + sort.selectedOptions[0].textContent.trim().toLowerCase());
          riseIn();
        });
      });
      views.addEventListener("submit", function (event) {
        event.preventDefault();
        refreshRegions("/clients?" + new URLSearchParams(new FormData(views, event.submitter)), ["results"], { atOnce: true });
      });
    }

    // Clients: the Needs you card. Without the script every item shows and Raise limit is a link that finds the
    // client. With it the list folds after three and an item can be dismissed. A dismissal lasts this page view and
    // this state of the client (the key holds both): nothing is remembered, and a client back at its limit is news.
    var FOLD_AFTER = 3;
    var dismissed = Object.create(null); // same reason as saves: a client's key can be any word
    var needsAll = false;
    var needs = null; // the card's parts and every item the server sent, shown or not

    function readNeeds() {
      var list = document.getElementById("needs-list");
      needs = list && {
        list: list,
        items: Array.from(list.querySelectorAll("[data-needs]")),
        seen: list.querySelector("[data-needs-seen]"),
        label: document.getElementById("needs-title"),
        more: document.querySelector("[data-needs-more]"),
      };
      Object.keys(dismissed).forEach(function (key) { // the client's state moved on: its old message no longer counts
        if (!needs || !needs.items.some(function (item) { return item.dataset.needs === key; })) delete dismissed[key];
      });
      showNeeds();
    }

    // Items leave the list rather than hide, so the first one shown has no divider above it.
    function showNeeds() {
      if (!needs) return;
      var left = needs.items.filter(function (item) { return !dismissed[item.dataset.needs]; });
      var folds = left.length > FOLD_AFTER;
      var shown = !left.length ? [needs.seen] : folds && !needsAll ? left.slice(0, FOLD_AFTER) : left;
      needs.seen.hidden = left.length > 0;
      needs.list.replaceChildren.apply(needs.list, shown);
      needs.list.querySelectorAll("[data-dismiss]").forEach(function (button) { button.hidden = false; });
      needs.label.classList.toggle("ds-card__label--warn", left.length > 0);
      needs.label.classList.toggle("ds-card__label--ok", !left.length);
      needs.label.querySelector("use").setAttribute("href", "#" + (left.length ? "i-warn" : "i-check"));
      var count = left.length === 1 ? "1 item" : left.length + " items";
      needs.label.querySelector(".ds-card__aside").textContent = left.length ? count : "";
      needs.more.hidden = !folds;
      needs.more.textContent = needsAll ? "Show fewer" : "Show all " + left.length;
      needs.more.setAttribute("aria-expanded", String(needsAll));
    }

    function dismissButtonOf(key) {
      var item = needs.list.querySelector("[data-needs='" + CSS.escape(key) + "']");
      return item && item.querySelector("[data-dismiss]");
    }

    function dismiss(button) {
      var item = button.closest("[data-needs]");
      var key = item.dataset.needs;
      var place = Array.from(needs.list.querySelectorAll("[data-dismiss]")).indexOf(button);
      dismissed[key] = true;
      showNeeds();
      var rest = needs.list.querySelectorAll("[data-dismiss]");
      (rest[Math.min(place, rest.length - 1)] || needs.label).focus();
      // The count and focus have moved on; the item goes back in its place only to fold away.
      needs.list.insertBefore(item, needs.list.children[place] || null);
      motion.remove(item, function () {
        if (dismissed[key]) item.remove(); // only this item: rebuilding the list would drop the focus just moved
      });
      showToast("Message hidden. It comes back when the page is reloaded.", function () {
        delete dismissed[key];
        showNeeds();
        hideToast();
        ((needs && dismissButtonOf(key)) || (needs && needs.label) || document.body).focus();
      });
    }

    document.addEventListener("click", function (event) { // on the page: a refresh replaces the card
      var target = event.target instanceof Element ? event.target : null;
      var control = target && target.closest("[data-dismiss], [data-needs-more]");
      if (!control) return;
      if ("dismiss" in control.dataset) dismiss(control);
      else {
        needsAll = !needsAll;
        showNeeds();
      }
    });

    readNeeds();
    motion.enter();

    // ---- The panel over a list (a client's or Add client over Clients; a staff member's or Add staff over Staff) ----
    // The server answers /clients/{key} and /clients/new (and /staff/{id} and /staff/new) with the list's page and the
    // panel open, so a saved link, a new tab and a reload all work. Here that panel becomes a modal dialog, and a click
    // on a link to one opens it in place: fetch the address, lift the panel out of the answer and show it, keeping the
    // address bar in step. One script for both screens: what differs between them is this table, keyed by the first part
    // of the address, and nothing else in the script names a screen.
    var PANEL_PAGES = {
      "/clients": {
        close: "/clients", // the list's own address: X, Cancel and a panel standing alone close to it
        panels: /^\/clients\/(new|[^/]+)$/, // a client or Add client; /clients/{key}/limit and /new/test are not
        add: "/clients/new",
        saves: /^\/clients\/(new|[^/]+\/(pause|resume|key|remove|channel))$/, // the forms that save; /new/test is the Test button's
        regions: ["summary", "results", "clients-count", "change-line"], // the parts of the list behind that a change can touch
        title: "Clients", // the page's title, before the suffix the server gives every page
        item: "Client", // an item's title, until its panel says it
        done: /^\?(added|removed)=/, // the redirect that means the panel is done
      },
      "/staff": {
        close: "/staff",
        panels: /^\/staff\/(new|\d+)$/,
        add: "/staff/new",
        saves: /^\/staff\/(new|\d+\/(edit|active))$/,
        regions: ["results", "staff-count", "who"], // "who" is the side menu's person: saving your own name changes it
        title: "Staff",
        item: "Staff member",
        done: /^\?added=/,
        undoPerItem: true, // a staff Undo acts on one member, so it can't be pressed from another member's panel
      },
    };
    var PAGE = PANEL_PAGES["/" + location.pathname.split("/")[1]]; // this page's row; none on History

    var panelRound = 0; // counts opens and closes: an answer from an older round is dropped
    var opened = null; // the open panel: { path, opener, pushed (this page made the history entry), title }
    var closing = false; // closed, and its transition still running
    var skeletonTimer = 0;
    var listTitle = document.title;
    var listAddress = PAGE && PAGE.panels.test(location.pathname) ? PAGE.close : location.pathname + location.search;

    function parts(dialog) { // the three parts a fill replaces; the toast and its regions sit beside them
      return Array.from(dialog.querySelectorAll(":scope > .ds-sheet__grabber, :scope > .ds-sheet__head, :scope > .ds-sheet__body"));
    }

    var MANAGED = ["data-state", "aria-labelledby", "aria-busy"]; // attributes a panel's own markup sets on the dialog

    // Replaces the panel's head, body and grabber with the fetched dialog's, and copies the dialog's own attributes
    // other than `open` (data-state: show() reads it to know whether a usage bar exists). Never touches the toast, its
    // regions or #undo. `sentAt`: how many saves had answered when the fetch was sent.
    function fillPanel(fresh, sentAt) {
      var incoming = parts(fresh).map(function (part) { return document.importNode(part, true); });
      parts(panel).forEach(function (part) { part.remove(); });
      panel.prepend.apply(panel, incoming);
      MANAGED.forEach(function (name) { panel.removeAttribute(name); });
      Array.from(fresh.attributes).forEach(function (attribute) {
        if (["open", "class", "id"].indexOf(attribute.name) < 0) panel.setAttribute(attribute.name, attribute.value);
      });
      wirePanel(panel, sentAt);
      if (toast.matches(":popover-open")) makeRoom(); // the new body keeps the room the old one did
    }

    function emptyPanel() { // nothing typed stays in the page: the token field goes with the rest
      parts(panel).forEach(function (part) { part.remove(); });
      MANAGED.forEach(function (name) { panel.removeAttribute(name); });
    }

    // What the page without this script has (a link, a button the switch makes needless) goes, as the Sort button
    // does: <noscript> shows nothing when the script is on but its file failed to load. Then the parts that need the
    // script: the bars, the saves, the pause question, the Test button and the Drafting switch. Run on the page at load,
    // and on each fill (the elements are new each time, so nothing is wired twice).
    function wirePanel(root, sentAt) {
      root.querySelectorAll("[data-no-script]").forEach(function (control) { control.hidden = true; });
      fillBars(root);
      adopt(root, sentAt || 0);
      wirePause(root);
      wireTest(root);
      // The client sheet's Drafting switch applies at once when the client is paused (resume needs no question).
      root.querySelectorAll("[data-submit-on-change]").forEach(function (control) {
        control.addEventListener("change", function () { control.form.requestSubmit(); });
      });
    }

    // Client sheet: turning Drafting off only asks (the confirm row, with the reason); Keep drafting puts it back.
    // Without the script, ?confirm=pause shows the same row.
    function wirePause(root) {
      var question = root.querySelector("#pause-confirm");
      if (!question) return;
      var run = root.querySelector("#run");
      run.addEventListener("change", function () {
        if (run.checked) { keep(); return; }
        question.hidden = false;
        run.setAttribute("aria-expanded", "true");
        document.getElementById("pause-reason").focus();
      });
      function keep() {
        question.hidden = true;
        run.checked = true;
        run.setAttribute("aria-expanded", "false");
        run.focus();
      }
      question.querySelector("[data-keep-drafting]").addEventListener("click", function (event) {
        event.preventDefault();
        keep();
      });
    }

    var ALERT_ICONS = { ok: "i-check", fail: "i-x", warn: "i-warn" }; // busy has the spinner instead
    var BUSY_WORDS = "Testing the connection…"; // a connection test takes seconds: the Test button and a save say it

    // The answer as a design system alert inside a status region, so it is heard as well as seen.
    function sayIn(result, words, tone) {
      var SVG = "http://www.w3.org/2000/svg";
      result.textContent = "";
      var alert = document.createElement("div");
      alert.className = "ds-alert ds-alert--" + tone;
      if (tone === "busy") {
        var spinner = document.createElement("span");
        spinner.className = "ds-alert__spinner";
        spinner.setAttribute("aria-hidden", "true");
        alert.appendChild(spinner);
      } else {
        var icon = document.createElementNS(SVG, "svg");
        icon.setAttribute("aria-hidden", "true");
        var use = document.createElementNS(SVG, "use");
        use.setAttribute("href", "#" + ALERT_ICONS[tone]);
        icon.appendChild(use);
        alert.appendChild(icon);
      }
      var body = document.createElement("div");
      body.className = "ds-alert__body";
      body.textContent = words;
      alert.appendChild(body);
      result.appendChild(alert);
    }

    // Add client and Replace token: the Test button asks POST /clients/new/test whether the key opens that HighLevel
    // account and says the answer in words. Nothing is saved; the form tests the key again before it saves.
    function wireTest(root) {
      var button = root.querySelector("#add-test");
      if (!button) return;
      var result = root.querySelector("#add-test-result");
      var form = button.form;
      var TONES = { works: "ok", wrong_account: "fail", unreachable: "warn" };

      function say(words, tone) { sayIn(result, words, tone); }

      // An answer speaks only for the details it tested: a change to either field clears it, and an answer still on
      // its way for the old details is dropped.
      var round = 0;
      [form.elements.location_id, form.elements.key].forEach(function (field) {
        field.addEventListener("input", function () { round += 1; result.textContent = ""; });
      });

      button.hidden = false;
      button.addEventListener("click", function () {
        var body = new URLSearchParams({ location_id: form.elements.location_id.value, key: form.elements.key.value });
        var mine = ++round;
        function sayIfCurrent(words, tone) { if (mine === round) say(words, tone); }
        button.disabled = true;
        say("Testing the connection…", "busy");
        fetch("/clients/new/test", { method: "POST", body: body })
          .then(function (response) { return response.json(); })
          .then(function (answer) {
            if (answer.errors) { // the location id or the key, checked before the agent is asked
              sayIfCurrent([answer.errors.location_id, answer.errors.key].filter(Boolean).join(" "), "fail");
            } else {
              sayIfCurrent(answer.message, TONES[answer.result] || "warn");
            }
          })
          .catch(function () { sayIfCurrent("Couldn't reach the admin app. Try again in a minute.", "warn"); })
          .then(function () { button.disabled = false; });
      });
    }

    wirePanel(document);

    // Leaving the page by a browser Back or a link closes nothing here, and a page kept for Forward (the back/forward
    // cache) would show a typed token again: every password field is emptied as the page goes. Nothing else is.
    window.addEventListener("pagehide", function () {
      document.querySelectorAll("input[type=password]").forEach(function (field) { field.value = ""; });
    });

    // The toast and its two live regions go into the open panel and come back out of it: a modal dialog makes
    // everything outside it inert, Undo included. Moving a showing popover hides it, so it is shown again.
    function placeToast(inside) {
      var showing = toast.matches(":popover-open");
      if (inside) inside.append(toast, polite, urgent);
      else panel.after(toast, polite, urgent);
      if (showing) {
        toast.showPopover();
        makeRoom();
      }
    }

    if (panel && PAGE) {
      var HEADING_SUFFIX = listTitle.indexOf(" · ") >= 0 ? listTitle.slice(listTitle.indexOf(" · ")) : "";

      // The nodes of the panel's skeleton: its title and X at once, from what the link said, and the lines of a
      // design-system skeleton in the body after 200 ms (a fast answer shouldn't flash one).
      function showShell(title) {
        var SVG = "http://www.w3.org/2000/svg";
        var grabber = document.createElement("div");
        grabber.className = "ds-sheet__grabber";
        grabber.setAttribute("aria-hidden", "true");
        var head = document.createElement("div");
        head.className = "ds-sheet__head";
        var heading = document.createElement("div");
        heading.className = "ds-sheet__heading";
        var h2 = document.createElement("h2");
        h2.className = "ds-sheet__title";
        h2.id = "panel-loading-title";
        h2.tabIndex = -1;
        h2.textContent = title;
        heading.appendChild(h2);
        var close = document.createElement("a");
        close.className = "ds-button ds-button--icon ds-button--tonal";
        close.href = PAGE.close;
        close.setAttribute("aria-label", "Close panel");
        var icon = document.createElementNS(SVG, "svg");
        icon.setAttribute("aria-hidden", "true");
        var use = document.createElementNS(SVG, "use");
        use.setAttribute("href", "#i-x");
        icon.appendChild(use);
        close.appendChild(icon);
        head.append(heading, close);
        var body = document.createElement("div");
        body.className = "ds-sheet__body";
        body.setAttribute("aria-busy", "true");
        parts(panel).forEach(function (part) { part.remove(); });
        panel.prepend(grabber, head, body);
        MANAGED.forEach(function (name) { panel.removeAttribute(name); });
        panel.setAttribute("aria-labelledby", h2.id);
        panel.setAttribute("aria-busy", "true");
      }

      function showSkeleton() {
        var body = panel.querySelector(".ds-sheet__body");
        if (!body) return;
        var lines = ["ds-skeleton--value", "ds-skeleton--line", "ds-skeleton--line ds-skeleton--medium",
                     "ds-skeleton--line ds-skeleton--short", "ds-skeleton--tile", "ds-skeleton--tile"];
        body.replaceChildren.apply(body, lines.map(function (kind) {
          var line = document.createElement("span");
          line.className = "ds-skeleton " + kind;
          line.setAttribute("aria-hidden", "true");
          return line;
        }));
      }

      function lockScroll(locked) { // the page stays still behind an open panel, set from script
        document.documentElement.style.overflow = locked ? "hidden" : "";
      }

      function titled(name) { return name + HEADING_SUFFIX; }

      // The title a link names for its panel: a client's or member's name, or "Add client" / "Add staff". Raise limit and
      // Review say it after a colon.
      function titleOf(link) {
        var label = link.getAttribute("aria-label") || "";
        var named = /^(?:Raise limit|Review): (.+)$/.exec(label);
        return named ? named[1] : link.textContent.trim();
      }

      // The panel of an address, as the server sends it, or an error. Only this app's own panel addresses (panelLink),
      // only an answer from this origin, parsed apart (DOMParser: its scripts never run); only the dialog leaves it.
      function fetchPanel(path) {
        return fetch(path, { headers: { Accept: "text/html" } })
          .then(function (response) {
            if (!response.ok || response.redirected || new URL(response.url).origin !== location.origin) {
              throw new Error("panel refused");
            }
            return response.text();
          })
          .then(function (html) {
            var fresh = new DOMParser().parseFromString(html, "text/html");
            var dialog = fresh.getElementById("panel");
            if (!dialog || dialog.localName !== "dialog" || !dialog.hasAttribute("open")) throw new Error("no panel");
            return { dialog: dialog, title: fresh.title };
          });
      }

      function focusIn(focusName) { // the control the link names, or the title
        var target = focusName === "limit" ? panel.querySelector(".ds-inline-edit__input")
          : focusName === "run" ? panel.querySelector("#run") : null;
        if (target) {
          target.focus();
          if (focusName === "limit") target.select();
        } else {
          var title = panel.querySelector(".ds-sheet__title");
          if (title) title.focus();
        }
      }

      // An Undo that acts on one item (a staff member's) goes when a panel opens for another item or for Add: it could be
      // pressed for someone out of view. The message stays and goes by itself, as any other without an Undo.
      function dropOtherUndo(path) {
        var form = document.getElementById("undo");
        if (!PAGE.undoPerItem || !form || new URL(form.action).pathname.indexOf(path + "/") === 0) return;
        form.remove();
        if (undo) {
          undo = null;
          undoButton.hidden = true;
          startTimer();
        }
      }

      // Opens the panel at once with the title the link names, then fills it from the server. `opener` is what to give
      // focus back to; `replaying` (Forward) means the history entry is already there.
      function openPanel(address, opener, focusName, replaying) {
        var round = ++panelRound;
        var sentAt = answers;
        dropOtherUndo(new URL(address, location.href).pathname);
        var state = replaying ? history.state : null;
        var title = opener ? titleOf(opener) : (state && state.title) || PAGE.item;
        if (!opened) listTitle = document.title;
        closing = false;
        clearTimeout(skeletonTimer);
        // `address` may carry a reveal's query (?confirm=key, ?confirm=channel, replayed by Back and Forward); the client is the path alone.
        opened = { path: new URL(address, location.href).pathname, opener: opener || null, pushed: true, title: title };
        if (!replaying) history.pushState({ panel: true, title: title }, "", address);
        document.title = titled(title);
        showShell(title);
        if (!panel.open) {
          panel.showModal();
          lockScroll(true);
          placeToast(panel);
        }
        focusIn(null);
        skeletonTimer = setTimeout(function () { if (round === panelRound) showSkeleton(); }, 200);
        fetchPanel(address).then(function (answer) {
          if (round !== panelRound) return; // closed, or another one opened since
          clearTimeout(skeletonTimer);
          fillPanel(answer.dialog, sentAt);
          document.title = answer.title;
          focusIn(focusName);
        }, function () {
          if (round !== panelRound) return;
          location.assign(address); // any failure loads the address as a page, as refreshRegions does
        });
      }

      // Focus goes back to what opened the panel; if a refresh replaced that, to the same item's link in the list;
      // otherwise to the page's heading.
      function backTo(opener, path) {
        if (opener && opener.isConnected) return opener;
        var links = Array.from(document.querySelectorAll("#results a[href]"));
        var same = links.filter(function (link) { return new URL(link.href).pathname === path; })[0];
        return same || document.querySelector(".ds-page-header__title");
      }

      // The panel goes: the page is live again, the toast comes back out, and once the transition has run the panel is
      // emptied, so nothing typed stays in the page. A newer open or close drops that emptying.
      function hideNow() {
        var was = opened;
        var round = ++panelRound;
        clearTimeout(skeletonTimer);
        opened = null;
        closing = true;
        stopDrag();
        lockScroll(false);
        document.title = listTitle;
        if (panel.open) panel.close();
        placeToast(null);
        var back = backTo(was.opener, was.path);
        if (back) back.focus();
        // The panel's own transitions: a slide, or a fade with reduced motion (the one thing emptying waits on).
        var running = panel.getAnimations({ subtree: true }).filter(function (animation) {
          return animation instanceof CSSTransition;
        });
        Promise.allSettled(running.map(function (animation) { return animation.finished; })).then(function () {
          if (round !== panelRound) return;
          closing = false;
          emptyPanel();
        });
      }

      var unwinding = 0; // history.back() calls of ours whose popstate hasn't come yet

      function closePanel() {
        if (!opened) return;
        if (!document.getElementById("results")) { // a refused form's panel stands alone: the list is a page away
          location.assign(PAGE.close);
          return;
        }
        var pushed = opened.pushed;
        hideNow();
        if (pushed) {
          unwinding += 1;
          history.back(); // the entry this page pushed goes; its popstate puts the list's address back
        } else {
          history.replaceState(null, "", listAddress); // after a direct load there is nothing to go back to
        }
      }

      // Back and Forward: the panel matches the address, and neither pushes anything.
      window.addEventListener("popstate", function () {
        if (unwinding) { // the close that asked for this: if a panel was opened since, the entry takes its address
          unwinding -= 1;
          if (opened) history.replaceState({ panel: true, title: opened.title }, "", opened.path);
          else if (location.pathname + location.search !== listAddress) history.replaceState(null, "", listAddress);
          return;
        }
        if (PAGE.panels.test(location.pathname)) {
          if (!opened || opened.path !== location.pathname) openPanel(location.pathname + location.search, null, null, true);
          return;
        }
        if (opened) hideNow();
        if (location.pathname + location.search !== listAddress) history.replaceState(null, "", listAddress);
      });

      // Anything typed in the panel and not yet sent (the limit field saves itself, and a switch only asks). A refused
      // form brings the typed values back as its defaults, and the token empty, so what is wrong with it counts too.
      function typedIn() {
        if (panel.querySelector("[aria-invalid='true'], .ds-alert[role=alert]")) return true;
        return Array.from(panel.querySelectorAll("input, textarea, select")).some(function (field) {
          if (field.matches(".ds-inline-edit__input, [type=hidden], [type=checkbox], [type=radio], [type=submit], [type=button]")) {
            return false;
          }
          if (field.localName === "select") {
            var chosen = Array.from(field.options).findIndex(function (option) { return option.defaultSelected; });
            return field.selectedIndex !== (chosen < 0 ? 0 : chosen); // with none marked, the first is the default
          }
          return field.value !== field.defaultValue;
        });
      }

      function panelLink(link) { // a link to an item's panel or Add, which the browser would load as a page
        if (link.target || link.hasAttribute("download")) return false;
        var url = new URL(link.href, location.href);
        return url.origin === location.origin && !url.search && !url.hash && PAGE.panels.test(url.pathname);
      }

      document.addEventListener("click", function (event) {
        if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return;
        var target = event.target instanceof Element ? event.target : null;
        if (!target || opened || panel.contains(target)) return; // those stay the browser's; the panel's own links are below
        var link = target.closest("a[href]");
        if (link) {
          if (!panelLink(link)) return;
          event.preventDefault();
          openPanel(new URL(link.href).pathname, link, link.dataset.focus || null);
          return;
        }
        // A List row opens its client too, unless the click was on one of its controls or after selecting text.
        var row = target.closest("#results tbody tr");
        if (!row || target.closest("button, input, select, textarea, label, summary") || String(getSelection()) !== "") return;
        var name = row.querySelector("a[href]");
        if (!name || !panelLink(name)) return;
        openPanel(new URL(name.href).pathname, name, null);
      });

      // Links in the panel: X, and Cancel, are links to the list that close it; the same client's reveal links, Cancel
      // and Keep client fill it. A link a handler of its own has taken (Keep drafting) is left to it.
      panel.addEventListener("click", function (event) {
        if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return;
        var link = event.target instanceof Element ? event.target.closest("a[href]") : null;
        if (!link) return;
        if (link.getAttribute("href") === PAGE.close) {
          event.preventDefault();
          closePanel();
        } else if (insideLink(link)) {
          event.preventDefault();
          followLink(link);
        }
      });

      // A press on the dimmed area closes it (a rectangle check: a press on the panel's padding or
      // its inert body targets the dialog too), unless something is typed in it: Add client holds four fields and a
      // token, and a stray click would lose them. X, Escape and a drag still close.
      panel.addEventListener("mousedown", function (event) {
        if (event.target !== panel || event.button !== 0) return;
        var box = panel.getBoundingClientRect();
        var outside = event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
        if (!outside || typedIn()) return;
        event.preventDefault(); // the press would move focus to the page after the panel gave it back
        closePanel();
      });

      panel.addEventListener("cancel", function (event) { // Escape: the panel closes through the same path as X
        event.preventDefault();
        closePanel();
      });
      // Closed by the browser itself. The event comes a task after close(), so a panel opened since is left alone.
      panel.addEventListener("close", function () { if (opened && !panel.open) closePanel(); });

      // ---- Dragging on phones ----
      // The head or the grabber follows the finger, with resistance upward and a flick sum. The
      // pointer is captured by the panel, not the part: a fill replaces the part while the finger is still down.

      var PHONE = window.matchMedia("(max-width: 720px)"); // the design system's bottom sheet
      var TAP = 4; // px: less than this is a tap with a wobble, whatever its speed
      var STILL = 150; // ms: a finger stopped this long before it let go isn't flicking, whatever its last move was
      var drag = null; // the drag under way: { pointer, startY, lastY, lastT, speed (px per ms) }

      function stopDrag() {
        drag = null;
        panel.classList.remove("ds-sheet--dragging");
        panel.style.transform = ""; // without the class, the design system's own transition takes it from here
      }

      panel.addEventListener("pointerdown", function (event) {
        if (drag || !opened || !PHONE.matches || event.button !== 0) return;
        if (!event.target.closest(".ds-sheet__grabber, .ds-sheet__head")) return;
        if (event.target.closest("a, button, input, select, textarea, label, summary")) return;
        drag = { pointer: event.pointerId, startY: event.clientY, lastY: event.clientY, lastT: event.timeStamp, speed: 0 };
        panel.classList.add("ds-sheet--dragging");
        panel.setPointerCapture(event.pointerId);
      });

      panel.addEventListener("pointermove", function (event) {
        if (!drag || event.pointerId !== drag.pointer) return;
        var dy = event.clientY - drag.startY;
        var y = dy > 0 ? dy : -(Math.abs(dy) * 40) / (40 + Math.abs(dy)); // an upward pull resists instead of stopping hard
        panel.style.transform = "translateY(" + y + "px)";
        var elapsed = event.timeStamp - drag.lastT;
        if (elapsed > 0) drag.speed = (event.clientY - drag.lastY) / elapsed;
        drag.lastY = event.clientY;
        drag.lastT = event.timeStamp;
      });

      panel.addEventListener("pointerup", function (event) {
        if (!drag || event.pointerId !== drag.pointer) return;
        var dy = event.clientY - drag.startY;
        var speed = event.timeStamp - drag.lastT > STILL ? 0 : drag.speed;
        var projected = dy + speed * 0.998 / (1 - 0.998); // where the flick would land (Apple's deceleration)
        var closes = Math.abs(dy) >= TAP && (projected > panel.getBoundingClientRect().height * 0.4 || speed > 0.6);
        stopDrag();
        if (closes) closePanel();
      });

      // The browser's own gestures on the same press: WebKit selects the title, and starts a native text drag from a
      // selection and never sends the pointer's end, so the drag would stick.
      panel.addEventListener("selectstart", function (event) { if (drag) event.preventDefault(); });
      panel.addEventListener("dragstart", function (event) { if (drag) event.preventDefault(); });

      // Never closes: the browser took the gesture back, so the person didn't let go.
      panel.addEventListener("pointercancel", function (event) {
        if (drag && event.pointerId === drag.pointer) stopDrag();
      });

      // ---- Saving inside the panel ----
      // The reveal links (Replace token, Change channel, Remove…) and their Cancel and Keep client fetch the same client's panel and fill
      // it, replacing the address's entry. A form is sent with fetch, url-encoded, to its own address, and the answer
      // decides what happens. A failed POST is never sent again: it may have been made.

      var NO_ANSWER = "Couldn't reach the admin app, so the change may not have been made. Reload the page to check.";
      var sending = null; // the one save on its way: a second is dropped

      function insideLink(link) { // the same client's reveal links, Cancel and Keep client
        if (!opened || opened.path === PAGE.add || link.target || link.hasAttribute("download")) return false;
        var url = new URL(link.href, location.href);
        return url.origin === location.origin && url.pathname === opened.path && !url.hash &&
          /^(\?confirm=(key|remove|channel))?$/.test(url.search);
      }

      function followLink(link) {
        var url = new URL(link.href);
        var address = url.pathname + url.search;
        var round = ++panelRound; // a newer fill, or a close, drops this answer
        var sentAt = answers;
        var was = panel.querySelector("form[action$='/key']") ? "key" : panel.querySelector("form[action$='/remove']") ? "remove"
          : panel.querySelector("form[action$='/channel']") ? "channel" : null;
        fetchPanel(address).then(function (answer) {
          if (round !== panelRound || !opened) return;
          fillPanel(answer.dialog, sentAt);
          if (!unwinding) history.replaceState({ panel: true, title: opened.title }, "", address);
          // The revealed form's first field, or the control that revealed the form just closed.
          var form = panel.querySelector("form[action$='/key'], form[action$='/remove'], form[action$='/channel']");
          var field = form && form.querySelector("input:not([type=hidden]), select, textarea");
          var back = !form && was && panel.querySelector("a[href$='?confirm=" + was + "']");
          (field || back || panel.querySelector(".ds-sheet__title")).focus();
        }, function () {
          if (round !== panelRound) return;
          location.assign(address);
        });
      }

      // A form to save by fetch: a POST, in the panel or the toast's Undo, to one of this app's own change addresses.
      // Any other form (a refused form's, sent by form.submit()) stays a page load.
      function sendable(form) {
        if (!(form instanceof HTMLFormElement) || form.method !== "post") return false;
        if (!panel.contains(form) && form.id !== "undo") return false;
        var url = new URL(form.action, location.href);
        return url.origin === location.origin && PAGE.saves.test(url.pathname);
      }

      document.addEventListener("submit", function (event) {
        if (event.defaultPrevented || !sendable(event.target)) return;
        event.preventDefault(); // first of all: a dropped or failed submit must never turn into a page load
        submitPanel(event.target, event.submitter);
      });

      // The toast and Undo an answer carries, said here: data-toast, and the form it names. Without a toast, nothing.
      function sayFrom(fresh) {
        var there = fresh.getElementById("toast");
        var message = there && there.dataset.toast;
        if (!message) return;
        var old = document.getElementById("undo");
        if (old) old.remove();
        var form = there.dataset.undo && fresh.getElementById(there.dataset.undo);
        // An answer that comes after another item's panel opened brings no Undo into it (see dropOtherUndo). With no panel
        // open, an Undo from the list is for the item the message names.
        if (form && PAGE.undoPerItem && opened && new URL(form.action, location.href).pathname.indexOf(opened.path + "/") !== 0) {
          form = null;
        }
        var mine = form && document.importNode(form, true);
        if (mine) document.body.append(mine);
        showToast(message, mine ? function () { mine.requestSubmit(); } : null);
      }

      // The refusal page's sentence (refused.html: a heading, then the message).
      function wordsOf(fresh) {
        var heading = fresh.querySelector("main h1");
        var message = heading && heading.nextElementSibling;
        return (message && message.localName === "p" && message.textContent.trim()) || NO_ANSWER;
      }

      function refreshList() { // the list behind: its summary row, its cards or rows, and the count; quiet on failure
        if (!document.getElementById("results")) return Promise.resolve(false);
        return refreshRegions(listAddress, PAGE.regions, { quiet: true });
      }

      // Where focus goes after a fill that a save caused: the control that did it, or the title. A staff member's Access
      // form shows the opposite button after Deactivate or Reactivate, and that is where focus goes.
      function focusAfterSave(form) {
        var action = new URL(form.action).pathname;
        var target = /\/(pause|resume)$/.test(action) ? panel.querySelector("#run")
          : /\/key$/.test(action) ? panel.querySelector("a[href$='?confirm=key']")
          : /\/channel$/.test(action) ? panel.querySelector("a[href$='?confirm=channel']")
          : /\/active$/.test(action) ? panel.querySelector("form[action$='/active'] button[type=submit]") : null;
        (target || panel.querySelector(".ds-sheet__title")).focus();
      }

      function focusProblem() { // the first invalid field, or the alert
        var target = panel.querySelector("[aria-invalid='true']");
        if (!target) {
          target = panel.querySelector(".ds-alert[role=alert]");
          if (target) target.tabIndex = -1;
        }
        (target || panel.querySelector(".ds-sheet__title")).focus();
      }

      // Typing that no save has made the database's, kept across the fill that a save elsewhere in the same panel causes.
      // `typing` reads it just before the fill: each text field whose value differs from its saved
      // value (defaultValue), by its form's address and its name. `putBack` puts it into the matching field after. Never
      // carried: a password field (the HighLevel token stays out of the page's memory), a field in a hidden form (the
      // panel would stay open over typing nobody can see), the form that saved (it shows the database's values), and a
      // field that saves itself (the limit). Called only for a fill of the same item, so another item never gets it.
      var NOT_CARRIED = ".ds-inline-edit__input, [type=hidden], [type=password], [type=checkbox], [type=radio], [type=submit], [type=button]";

      function typingFields(form) { // the fields of a visible form that can hold typing
        if (form.closest("[hidden]")) return [];
        return Array.from(form.querySelectorAll("input, textarea")).filter(function (field) {
          return field.name && !field.matches(NOT_CARRIED);
        });
      }

      function typing(sent) { // `sent`: the address of the form that saved
        var found = [];
        panel.querySelectorAll("form").forEach(function (form) {
          var action = new URL(form.action).pathname;
          if (action === sent) return;
          typingFields(form).forEach(function (field) {
            if (field.value !== field.defaultValue) found.push({ action: action, name: field.name, value: field.value });
          });
        });
        return found;
      }

      function putBack(found) {
        found.forEach(function (item) {
          panel.querySelectorAll("form").forEach(function (form) {
            if (new URL(form.action).pathname !== item.action) return;
            typingFields(form).forEach(function (field) { if (field.name === item.name) field.value = item.value; });
          });
        });
      }

      function submitPanel(form, submitter) {
        if (sending && sending.round === panelRound) return; // one at a time in a panel; another panel's save is its own
        var here = !!opened; // a panel is open: its body waits for the answer, and the answer may fill it
        // A save from an open panel is newer than any reveal still on its way, which must not replace the inert body. With
        // no panel open (an Undo after a close) the round stays: hideNow's emptying waits on it.
        var round = here ? ++panelRound : panelRound;
        var path = here ? opened.path : null;
        var sentAt = answers;
        var body = here ? panel.querySelector(".ds-sheet__body") : null;
        var control = submitter || (form.contains(document.activeElement) ? document.activeElement : null);
        // Forms holding a key take seconds (the connection test): they say so, as the Test button does.
        var result = form.querySelector("input[type=password]") ? panel.querySelector("#add-test-result") : null;
        var request = new URLSearchParams(new FormData(form, submitter));

        var mine = sending = { round: round };
        if (body) {
          body.inert = true; // nothing can be typed that the answer would overwrite; the head stays live, so X works
          var title = panel.querySelector(".ds-sheet__title");
          if (title) title.focus();
        }
        var busyTimer = 0;
        if (result) {
          sayIn(result, BUSY_WORDS, "busy");
          announce(BUSY_WORDS, false); // the visible message sits in the inert body, which a screen reader skips
          busyTimer = announcing;
        }

        function release() { // the panel as it was, to be given its focus back
          if (sending === mine) sending = null; // only its own: another panel's save may be on its way
          if (body) body.inert = false;
          if (result && result.isConnected) result.textContent = "";
          if (result) {
            clearTimeout(busyTimer);
            if (polite.textContent === BUSY_WORDS) polite.textContent = ""; // a toast's own words stay
          }
        }
        function stillThere() { return here && !!opened && round === panelRound; }
        function lost(words) { // no answer, or one that isn't ours: the panel stays, with what was typed
          release();
          // The token is not kept either, as when a refusal brings the form back; the other typed values stay.
          form.querySelectorAll("input[type=password]").forEach(function (field) { field.value = ""; });
          // The Drafting switch sent at once: it goes back to what the head still says, so turning it off isn't a second send.
          form.querySelectorAll("[data-submit-on-change]").forEach(function (field) { field.checked = false; });
          showToast(words, null, true);
          if (stillThere() && control && control.isConnected) control.focus();
        }

        function answered(response, html) {
          var url = new URL(response.url);
          if (url.origin !== location.origin) { lost(NO_ANSWER); return; }
          var fresh = new DOMParser().parseFromString(html, "text/html");
          var dialog = fresh.getElementById("panel"); // every answer of this page's screen holds one; only a panel's is open
          var showsPanel = !!dialog && dialog.localName === "dialog" && dialog.hasAttribute("open");
          var redirected = response.ok && response.redirected;

          if (redirected && showsPanel && PAGE.panels.test(url.pathname) && url.pathname !== PAGE.add) {
            // To the same item: its panel as the change left it, the toast and Undo it carries, the list behind.
            release();
            // A refused form's panel stands alone, at the form's own address: nothing here to fill, and its field still
            // holds what was typed. The item's page, with its toast, is a load away.
            if (stillThere() && !document.getElementById("results")) { location.replace(url.href); return; }
            if (stillThere() && url.pathname === path) {
              var typed = typing(new URL(form.action).pathname);
              fillPanel(dialog, sentAt);
              putBack(typed);
              // A person's name can change, which a client's can't: the document's title and the entry's follow the answer.
              var named = dialog.querySelector(".ds-sheet__title").textContent.trim();
              var renamed = !!opened.title && named !== opened.title;
              if (renamed) opened.title = named;
              if (fresh.title !== document.title) document.title = fresh.title;
              if ((location.search || renamed) && !unwinding) history.replaceState({ panel: true, title: opened.title }, "", path);
              focusAfterSave(form);
            }
            sayFrom(fresh);
            refreshList();
            return;
          }

          if (redirected && !showsPanel && url.pathname === PAGE.close && PAGE.done.test(url.search)) {
            // Add client, Add staff or Remove: the panel is done. A list with no client yet, or none left, has other parts
            // than the regions to fill, so that one loads as a page, with its toast.
            release();
            var regionsThere = PAGE.regions.every(function (id) { return document.getElementById(id) && fresh.getElementById(id); });
            if (!regionsThere && stillThere()) { location.replace(url.href); return; }
            var closedHere = stillThere();
            if (closedHere) {
              hideNow();
              if (!unwinding) history.replaceState(null, "", listAddress); // the entry the panel had shows the list
            }
            sayFrom(fresh);
            refreshList().then(function () {
              // Remove took the opener with it: the heading is where focus goes.
              var heading = document.querySelector(".ds-page-header__title");
              if (closedHere && heading && (!document.activeElement || document.activeElement === document.body)) heading.focus();
            });
            return;
          }

          if (!response.ok && showsPanel) { // 422, 409, 429, 503 for Slack: the form again, with what was typed
            release();
            if (stillThere() && panel.contains(form)) {
              var typedElsewhere = typing(new URL(form.action).pathname);
              fillPanel(dialog, sentAt);
              putBack(typedElsewhere);
              focusProblem();
            } else { // the panel was closed meanwhile: not silent, and what was typed is gone with it
              var alertBody = dialog.querySelector(".ds-alert[role=alert] .ds-alert__body");
              showToast((alertBody && alertBody.textContent.trim()) || "That wasn't saved. Open it again to try once more.", null, true);
            }
            return;
          }

          if (!response.ok && !showsPanel) { lost(wordsOf(fresh)); return; } // 403, 404, a database 503
          lost(NO_ANSWER);
        }

        fetch(form.action, { method: "POST", body: request, headers: { Accept: "text/html" } })
          .then(function (response) {
            return response.text().then(function (html) { return { response: response, html: html }; });
          })
          .then(function (got) { answered(got.response, got.html); }, function () { lost(NO_ANSWER); })
          .finally(function () { if (sending === mine) sending = null; if (body) body.inert = false; });
      }

      // A panel the server sent open (a saved link, a reload, a refused form) becomes the modal one, with its opening
      // transition off for this first frame so it doesn't slide in.
      if (panel.open && !panel.matches(":modal")) {
        opened = { path: location.pathname, opener: null, pushed: false, title: "" };
        listTitle = PAGE.title + HEADING_SUFFIX;
        panel.classList.add("ds-sheet--dragging");
        panel.close();
        panel.showModal();
        lockScroll(true);
        placeToast(panel);
        focusIn(null);
        requestAnimationFrame(function () {
          requestAnimationFrame(function () { panel.classList.remove("ds-sheet--dragging"); });
        });
      }
    }
  });

})();
