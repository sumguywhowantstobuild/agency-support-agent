// Page-change glides use the root's timing (view-transition layers hang off the page root, out of a container's
// reach); numbers, bars, entrances and removals follow the element's own --ds-motion-* timing.
(function () {
  var DEFAULT_EASING = "cubic-bezier(0.32, 0.72, 0, 1)";

  // "280ms" or "0.28s" as milliseconds, else the fallback.
  function parseDuration(value, fallback) {
    var m = /^\s*(\d*\.?\d+)(ms|s)\s*$/.exec(value || "");
    return m ? parseFloat(m[1]) * (m[2] === "s" ? 1000 : 1) : fallback;
  }

  // "12px" as 12, else the fallback (a distance token, never negative).
  function parseLength(value, fallback) {
    var m = /^\s*(\d*\.?\d+)px\s*$/.exec(value || "");
    return m ? parseFloat(m[1]) : fallback;
  }

  // The four numbers of a cubic-bezier(); bad input gives the system curve.
  function curve(value) {
    var m = /^\s*cubic-bezier\(\s*([\d.-]+)\s*,\s*([\d.-]+)\s*,\s*([\d.-]+)\s*,\s*([\d.-]+)\s*\)\s*$/.exec(value || "");
    var p = m && m.slice(1).map(Number);
    return p && p.every(isFinite) && p[0] >= 0 && p[0] <= 1 && p[2] >= 0 && p[2] <= 1 ? p : curve(DEFAULT_EASING);
  }

  // The curve as a function of time 0..1.
  function parseEasing(value) {
    var p = curve(value);
    function axis(a, b, t) {
      return 3 * (1 - t) * t * ((1 - t) * a + t * b) + t * t * t;
    }
    return function (x) {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      var lo = 0, hi = 1, t = x;
      for (var i = 0; i < 30; i++) {
        var v = axis(p[0], p[2], t);
        if (Math.abs(v - x) < 1e-6) break;
        if (v < x) lo = t; else hi = t;
        t = (lo + hi) / 2;
      }
      return axis(p[1], p[3], t);
    };
  }

  // The first number in a text (a minus sign right before the digits belongs to it).
  var NUMBER = /-?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)/;

  function readNumber(text) {
    var m = NUMBER.exec(text);
    return m ? parseFloat(m[0].replace(/,/g, "")) : null;
  }

  // The final `text` with its number set `eased` (0..1) of the way from `from` to `to`.
  function tickerText(text, from, to, eased) {
    return text.replace(NUMBER, function (shape) {
      var decimals = (shape.split(".")[1] || "").length;
      var value = from + (to - from) * eased;
      var parts = Math.abs(value).toFixed(decimals).split(".");
      var negative = value < 0 && parseFloat(parts.join(".")) !== 0;
      if (shape.indexOf(",") >= 0) parts[0] = parts[0].replace(/\B(?=(\d{3})+$)/g, ",");
      return (negative ? "-" : "") + parts.join(".");
    });
  }

  function noop() {}

  function createMotion(win) {
    var doc = win.document;

    var reduced = win.matchMedia && win.matchMedia("(prefers-reduced-motion: reduce)");

    function all(selector) {
      return Array.from(doc.querySelectorAll(selector));
    }

    // Run fn now, then the animations `after` starts; resolves when they end, rejects with fn's error.
    function plain(fn, after) {
      try {
        fn();
        return Promise.all(after ? after() : []).then(noop);
      } catch (error) {
        return Promise.reject(error);
      }
    }

    // Timing rules in a constructed sheet (no <style>: a strict CSP allows it); root apart from `*.ds-item`.
    if (doc.adoptedStyleSheets && win.CSSStyleSheet) {
      var sheet = new win.CSSStyleSheet();
      var timed = "{animation-duration:var(--ds-motion-duration-default,280ms);" +
        "animation-timing-function:var(--ds-motion-easing," + DEFAULT_EASING + ")}";
      sheet.replaceSync(
        "::view-transition-group(*.ds-item),::view-transition-old(*.ds-item),::view-transition-new(*.ds-item)" +
          timed + "::view-transition-group(root),::view-transition-old(root),::view-transition-new(root)" + timed
      );
      doc.adoptedStyleSheets = doc.adoptedStyleSheets.concat(sheet);
    }

    function keyed() {
      return all("[data-ds-key]");
    }

    var tagged = new Set(), live = [], pending = 0, tail;

    // Order: each fn runs only after every fn handed to update before it has run. `pending` counts those not
    // yet run, `tail` settles when the last of them has; the count drops after a fn has run, never before.
    function inTurn(run) {
      var gate = pending && tail, release;
      pending++;
      tail = new Promise((resolve) => (release = resolve));
      return function () {
        var go = function () {
          try {
            return run();
          } finally {
            pending--;
            release();
          }
        };
        return gate ? gate.then(go) : go();
      };
    }

    // Name keyed items so the browser can follow them.
    function tag(items) {
      items.forEach(function (el) {
        el.style.setProperty("view-transition-name", "ds-" + win.CSS.escape(el.getAttribute("data-ds-key")));
        el.style.setProperty("view-transition-class", "ds-item");
        tagged.add(el);
      });
    }

    // Names come off when the last running transition has finished.
    function finish(transition) {
      live.splice(live.indexOf(transition), 1);
      if (live.length) return;
      tagged.forEach(function (el) {
        el.style.removeProperty("view-transition-name");
        el.style.removeProperty("view-transition-class");
      });
      tagged.clear();
    }

    function hasDuplicates(items) {
      var seen = new Set();
      return items.some(function (el) {
        var key = el.getAttribute("data-ds-key");
        return seen.has(key) || !seen.add(key);
      });
    }

    // Durations and curve from the --ds-motion-* properties as this element sees them (they inherit, so a
    // mode or container override applies); a page without them gets the defaults.
    function timing(el) {
      var style = win.getComputedStyle(el);
      var duration = function (name, fallback) {
        return parseDuration(style.getPropertyValue("--ds-motion-duration-" + name), fallback);
      };
      var p = curve(style.getPropertyValue("--ds-motion-easing"));
      var css = "cubic-bezier(" + p + ")";
      return { fast: duration("fast", 140), base: duration("default", 280), sheet: duration("sheet", 420),
        fill: duration("fill", 600), stagger: duration("stagger", 400),
        rise: parseLength(style.getPropertyValue("--ds-motion-stagger-offset"), 12),
        ease: parseEasing(css), css: css };
    }

    // Stop functions of what runs: rolls (tickers, bars) and folds (enter, remove).
    var rolls = new Set(), folds = new Set();

    function stop(set) {
      set.forEach((f) => f());
    }

    function track(set, f, finished) {
      set.add(f);
      return finished.then(noop, noop).then(() => set.delete(f));
    }

    // Reduced motion: all ends now (rolls show final text, a fold still calls done).
    function stopAll() {
      stop(rolls);
      stop(folds);
      live.forEach((transition) => transition.skipTransition());
    }
    if (reduced && reduced.addEventListener) {
      reduced.addEventListener("change", () => reduced.matches && stopAll());
    }

    function snapshot() {
      return {
        tickers: all("[data-ds-ticker]").map((el) => ({ el, id: el.getAttribute("data-ds-ticker"), from: readNumber(el.textContent) })),
        bars: all("[data-ds-grow]").map((el) => ({ el, id: el.getAttribute("data-ds-grow"), width: el.getBoundingClientRect().width })),
      };
    }

    // After fn: the element with the id the old one had before fn (a re-render makes new ones); with no id, the
    // same element while it still has none. Nothing when that record is gone: an element reused for another
    // record has no old value to roll from, so it just shows its new one.
    function counterpart(old, attr, id) {
      if (!id) return old.getAttribute(attr) ? null : old;
      return all("[" + attr + "]").filter((el) => el.getAttribute(attr) === id)[0] || null;
    }

    // Roll tickers and grow bars from where they were; promises settle when they end.
    function settle(before) {
      var promises = [];
      before.tickers.forEach(function (s) {
        var el = counterpart(s.el, "data-ds-ticker", s.id);
        if (!el) return;
        var text = el.textContent, to = readNumber(text), t = timing(el);
        if (!el.isConnected || el.children.length || s.from === null || to === null || s.from === to || !t.fill) return;
        promises.push(new Promise(function (resolve) {
          var start = null, id;
          function end() {
            win.cancelAnimationFrame(id);
            rolls.delete(end);
            el.textContent = text;
            resolve();
          }
          function tick(now) {
            if (start === null) start = now;
            var p = (now - start) / t.fill;
            if (p >= 1) return end();
            el.textContent = tickerText(text, s.from, to, t.ease(p));
            id = win.requestAnimationFrame(tick);
          }
          rolls.add(end);
          el.textContent = tickerText(text, s.from, to, 0);
          id = win.requestAnimationFrame(tick);
        }));
      });
      before.bars.forEach(function (s) {
        var el = counterpart(s.el, "data-ds-grow", s.id);
        if (!el || !el.isConnected || !el.animate) return;
        var t = timing(el), width = el.getBoundingClientRect().width;
        if (Math.abs(width - s.width) < 0.5 || !t.fill) return;
        // Scaled about the origin, moved so the left edge stays put. Scale cannot start from nothing
        // (it would need an infinite ratio), so a bar that ends at zero animates its width instead.
        var r = s.width / width, o = parseFloat(win.getComputedStyle(el).transformOrigin) || 0;
        var grow = el.animate(
          width
            ? [{ transform: "translateX(" + o * (r - 1) + "px) scaleX(" + r + ")" }, { transform: "translateX(0px) scaleX(1)" }]
            : [{ width: s.width + "px" }, { width: "0px" }],
          { duration: t.fill, easing: t.css, composite: width ? "add" : "replace" }
        );
        promises.push(track(rolls, () => grow.cancel(), grow.finished));
      });
      return promises;
    }

    function update(fn) {
      if (still()) {
        stopAll();
        return inTurn(() => plain(fn))(); // at once when nothing is pending
      }
      var items = keyed(), before, animations = [];
      // Just before fn: note where tickers and bars are, end rolls still going.
      var begin = function () {
        before = snapshot();
        stop(rolls);
      };
      if (!doc.startViewTransition || hasDuplicates(items)) { // a duplicate name would abort a transition
        // Queued behind a transition, this runs later: reduced motion is rechecked here, as in the transition below.
        return inTurn(() => plain(function () { begin(); fn(); }, function () { return still() ? [] : settle(before); }))();
      }
      tag(items);
      // The browser runs the callback even for a skipped transition, so the turn is always released.
      var transition = doc.startViewTransition(inTurn(function () {
        begin();
        fn();
        tag(keyed());
        animations = still() ? [] : settle(before);
      }));
      live.push(transition);
      transition.ready.catch(noop); // skipped is not an error
      return transition.finished
        .finally(() => finish(transition))
        .then(() => Promise.all(animations));
    }

    function still() {
      return reduced && reduced.matches;
    }

    // Fold the element away (the rest slide up), then done removes it.
    function remove(el, done) {
      var t = timing(el), height = still() || !el.animate ? NaN : parseFloat(win.getComputedStyle(el).height);
      if (isNaN(height) || !t.base) return done && done();
      var overflow = el.style.overflow, over = false;
      var end = function () {
        if (over) return;
        over = true;
        folds.delete(end);
        if (done) done();
        fold.cancel();
        el.style.overflow = overflow;
      };
      el.style.overflow = "hidden";
      var zero = { opacity: 0 };
      // Everything that could keep the space (a minimum height, borders, padding, margins) goes to nothing.
      ["height", "minHeight", "marginTop", "marginBottom", "paddingTop", "paddingBottom", "borderTopWidth", "borderBottomWidth"]
        .forEach((name) => (zero[name] = "0px"));
      var fold = el.animate([{ height: height + "px" }, zero], { duration: t.base, easing: t.css, fill: "forwards" });
      folds.add(end);
      fold.finished.then(end, end);
    }

    // Children rise in one after another, the last starting within --ds-motion-duration-stagger of the
    // first, each from --ds-motion-stagger-offset (12px) below its place.
    var rising = new WeakMap();
    function enter(container) {
      var parents = container ? [container] : all("[data-ds-stagger]");
      var promises = [];
      if (!still()) {
        parents.forEach(function (parent) {
          var kids = Array.from(parent.children);
          kids.forEach(function (kid, i) {
            // A ripple still going stops first, so its in-between state is not read as the child's own style.
            if (rising.has(kid)) rising.get(kid).cancel();
            var t = timing(kid), own = win.getComputedStyle(kid);
            if (!kid.animate || !t.sheet) return;
            var step = Math.min(t.fast, t.stagger / Math.max(kids.length - 1, 1));
            var rise = kid.animate([
              { opacity: 0, transform: (own.transform === "none" ? "" : own.transform + " ") + "translateY(" + t.rise + "px)" },
              { opacity: own.opacity, transform: own.transform },
            ], {
              duration: t.sheet,
              easing: t.css,
              delay: i * step,
              fill: "backwards",
            });
            rising.set(kid, rise);
            promises.push(track(folds, () => rise.cancel(), rise.finished));
          });
        });
      }
      return Promise.all(promises).then(noop);
    }

    return { update: update, enter: enter, remove: remove };
  }

  var helpers = {
    parseDuration: parseDuration,
    parseLength: parseLength,
    parseEasing: parseEasing,
    readNumber: readNumber,
    tickerText: tickerText,
    createMotion: createMotion,
  };
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else window.dsMotion = createMotion(window);
})();
