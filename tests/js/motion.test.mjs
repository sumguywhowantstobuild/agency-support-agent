import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const motion = createRequire(import.meta.url)("../../src/support_agent/admin/static/ds/ds-motion.js");

test("a duration custom property is read as milliseconds", () => {
  assert.equal(motion.parseDuration("280ms", 1), 280);
  assert.equal(motion.parseDuration(" 0.42s ", 1), 420);
  assert.equal(motion.parseDuration("0ms", 1), 0);
});

test("a missing or unreadable duration falls back", () => {
  assert.equal(motion.parseDuration("", 140), 140);
  assert.equal(motion.parseDuration(undefined, 140), 140);
  assert.equal(motion.parseDuration("soon", 140), 140);
  assert.equal(motion.parseDuration("-5ms", 140), 140);
});

test("a pixel length custom property is read as a number of pixels, else the fallback", () => {
  assert.equal(motion.parseLength("12px", 1), 12);
  assert.equal(motion.parseLength(" 8px ", 1), 8);
  assert.equal(motion.parseLength("0px", 1), 0);
  assert.equal(motion.parseLength("1.5px", 1), 1.5);
  for (const bad of ["", undefined, "far", "-5px", "12", "1rem", "12em"]) assert.equal(motion.parseLength(bad, 12), 12, String(bad));
});

const close = (actual, expected, message) =>
  assert.ok(Math.abs(actual - expected) < 0.002, `${message}: ${actual} is not near ${expected}`);

test("the curve is read from a cubic-bezier value", () => {
  const linear = motion.parseEasing("cubic-bezier(0, 0, 1, 1)");
  for (const x of [0, 0.25, 0.5, 0.9, 1]) close(linear(x), x, `linear at ${x}`);

  const ease = motion.parseEasing(" cubic-bezier(0.32, 0.72, 0, 1) ");
  close(ease(0), 0, "start");
  close(ease(1), 1, "end");
  // The system curve is a strong ease-out: well past halfway at half the time.
  assert.ok(ease(0.5) > 0.9 && ease(0.5) < 0.99, `at half the time: ${ease(0.5)}`);
  let last = 0;
  for (let i = 1; i <= 20; i++) {
    const y = ease(i / 20);
    assert.ok(y >= last, "never goes backwards");
    last = y;
  }
});

test("a curve that cannot be read falls back to the system curve", () => {
  const system = motion.parseEasing("cubic-bezier(0.32, 0.72, 0, 1)");
  for (const bad of ["", undefined, "ease", "cubic-bezier(1, 2)", "cubic-bezier(2, 0, 0, 1)", "cubic-bezier(a, b, c, d)",
    "cubic-bezier(0, -, 1, 1)", "cubic-bezier(0, 1.2.3, 1, 1)", "cubic-bezier(0, 0, 1, .)"]) {
    const ease = motion.parseEasing(bad);
    for (const x of [0.1, 0.5, 0.8]) close(ease(x), system(x), `${bad} at ${x}`);
  }
});

test("a ticker's in-between text swaps only its number", () => {
  // Halfway from 30 to 40 is 35; the words around it stay.
  assert.equal(motion.tickerText("of 40 this hour", 30, 40, 0.5), "of 35 this hour");
  assert.equal(motion.tickerText("of 40 this hour", 30, 40, 0), "of 30 this hour");
  assert.equal(motion.tickerText("of 40 this hour", 30, 40, 1), "of 40 this hour");
  // Rolling down works too, and decimals and thousands separators keep the final text's shape.
  assert.equal(motion.tickerText("12 left", 20, 12, 0.5), "16 left");
  assert.equal(motion.tickerText("1.5 GB", 1, 1.5, 0.5), "1.3 GB");
  assert.equal(motion.tickerText("1,200 sent", 0, 1200, 0.5), "600 sent");
  assert.equal(motion.tickerText("12,000 sent", 0, 12000, 0.5), "6,000 sent");
});

test("a ticker's number is read from its text", () => {
  assert.equal(motion.readNumber("of 30 this hour"), 30);
  assert.equal(motion.readNumber("1,240 drafts"), 1240);
  assert.equal(motion.readNumber("2.5 GB"), 2.5);
  assert.equal(motion.readNumber("no number"), null);
});

// ---- update(): a fake window, so no browser is needed ----------------------------------------

function fakeElement(attrs = {}, extra = {}) {
  const style = new Map();
  return {
    attrs,
    style: {
      setProperty: (name, value) => style.set(name, value),
      removeProperty: (name) => style.delete(name),
      values: style,
    },
    getAttribute: (name) => (name in attrs ? attrs[name] : null),
    textContent: "",
    children: [],
    isConnected: true,
    ...extra,
  };
}

// dom lists what each marker currently matches; a test changes them inside fn to mimic a re-render.
function fakeWorld({ reduced = false, transitions = true, css = {} } = {}) {
  const dom = { key: [], ticker: [], grow: [], stagger: [] };
  const log = [];
  const frames = [];
  let frameId = 0;
  // The reduced-motion preference: a test flips `matches` and calls `change()` as the browser would.
  const media = {
    matches: reduced,
    listeners: [],
    addEventListener: (type, listener) => media.listeners.push(listener),
    change: (matches) => {
      media.matches = matches;
      media.listeners.forEach((listener) => listener({ matches }));
    },
  };
  const document = {
    documentElement: {},
    adoptedStyleSheets: [],
    querySelectorAll: (selector) => dom[/data-ds-(\w+)/.exec(selector)[1]].slice(),
  };
  if (transitions) {
    document.startViewTransition = (callback) => {
      log.push("transition starts");
      const updateCallbackDone = Promise.resolve().then(callback);
      const finished = updateCallbackDone.then(() => new Promise((resolve) => setTimeout(resolve, 0)));
      return { ready: updateCallbackDone, updateCallbackDone, finished, skipTransition: () => log.push("skipped") };
    };
  }
  const window = {
    document,
    matchMedia: () => media,
    getComputedStyle: (element) => ({
      getPropertyValue: (name) => (element.css && name in element.css ? element.css[name] : css[name]) || "",
      height: element.height,
      transform: element.transform || "none",
      opacity: element.opacity || "1",
      // Resolved to pixels, as a browser does; the default is the centre of the box.
      transformOrigin: element.origin || `${(element.width || 0) / 2}px 5px`,
    }),
    CSS: { escape: (text) => text.replace(/[^\w-]/g, (c) => "\\" + c) },
    CSSStyleSheet: class {
      replaceSync(text) {
        this.text = text;
      }
    },
    requestAnimationFrame: (callback) => {
      callback.frameId = ++frameId;
      frames.push(callback);
      return callback.frameId;
    },
    cancelAnimationFrame: (id) => {
      const at = frames.findIndex((callback) => callback.frameId === id);
      if (at >= 0) frames.splice(at, 1);
    },
  };
  return { window, document, dom, log, frames, media };
}

test("reduced motion runs fn once, at once, with no transition", async () => {
  const world = fakeWorld({ reduced: true });
  const api = motion.createMotion(world.window);
  let runs = 0;

  const promise = api.update(() => runs++);

  assert.equal(runs, 1); // already run, before anything is awaited
  await promise;
  assert.equal(runs, 1);
  assert.deepEqual(world.log, []);
});

test("with motion allowed, fn runs once inside a view transition and update waits for it", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  let runs = 0;

  const promise = api.update(() => {
    runs++;
    world.log.push("fn runs");
  });

  assert.equal(runs, 0); // the browser snapshots the old state first, then runs fn
  await promise;
  assert.equal(runs, 1);
  assert.deepEqual(world.log, ["transition starts", "fn runs"]);
});

test("an error thrown by fn rejects update, in a transition and without one", async () => {
  const boom = new Error("boom");
  for (const options of [{}, { reduced: true }, { transitions: false }]) {
    const api = motion.createMotion(fakeWorld(options).window);
    await assert.rejects(
      api.update(() => {
        throw boom;
      }),
      boom,
      JSON.stringify(options),
    );
  }
});

test("keyed items are named for one transition only: old state, new state, then cleared", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const kept = fakeElement({ "data-ds-key": "client 7" });
  const gone = fakeElement({ "data-ds-key": "client:8" });
  const added = fakeElement({ "data-ds-key": "client-9" });
  world.dom.key = [kept, gone];
  const named = (element) => [
    element.style.values.get("view-transition-name"),
    element.style.values.get("view-transition-class"),
  ];
  const seen = {};
  const startView = world.document.startViewTransition;
  world.document.startViewTransition = (callback) => {
    seen.keptBefore = named(kept); // named before the old state is captured
    seen.goneBefore = named(gone);
    return startView(callback);
  };

  await api.update(() => {
    world.dom.key = [kept, added]; // the app re-renders: one item went, one is new
    seen.addedDuringFn = named(added); // not yet named while fn runs...
    queueMicrotask(() => (seen.addedAfterFn = named(added)));
  });

  assert.deepEqual(seen.keptBefore, ["ds-client\\ 7", "ds-item"]); // names are escaped CSS identifiers
  assert.deepEqual(seen.goneBefore, ["ds-client\\:8", "ds-item"]);
  assert.deepEqual(seen.addedDuringFn, [undefined, undefined]);
  assert.deepEqual(seen.addedAfterFn, ["ds-client-9", "ds-item"]); // ...named in the new state
  for (const element of [kept, gone, added]) assert.deepEqual(named(element), [undefined, undefined]);
});

const later = () => new Promise((resolve) => setTimeout(resolve, 5));
const frame = (world, time) => world.frames.splice(0).forEach((callback) => callback(time));

test("a ticker rolls from its old number to the new one and ends on exactly the text fn wrote", async () => {
  const world = fakeWorld({ css: { "--ds-motion-duration-fill": "600ms" } });
  const api = motion.createMotion(world.window);
  const ticker = fakeElement({}, { textContent: "of 30 this hour" });
  const untouched = fakeElement({}, { textContent: "7 paused" });
  world.dom.ticker = [ticker, untouched];
  let done = false;

  const promise = api.update(() => (ticker.textContent = "of 40 this hour")).then(() => (done = true));
  await later();

  assert.equal(ticker.textContent, "of 30 this hour"); // starts where it was, no jump to the new number
  frame(world, 1000); // the first frame fixes the start time
  frame(world, 1100); // 100 ms of 600
  const ease = motion.parseEasing("cubic-bezier(0.32, 0.72, 0, 1)");
  assert.equal(ticker.textContent, motion.tickerText("of 40 this hour", 30, 40, ease(100 / 600)));
  assert.equal(done, false);
  frame(world, 1700); // past the end
  await promise;
  assert.equal(ticker.textContent, "of 40 this hour");
  assert.equal(untouched.textContent, "7 paused");
  assert.equal(world.frames.length, 0); // no frames left running
});

function fakeBar(width) {
  const bar = fakeElement();
  bar.width = width;
  bar.getBoundingClientRect = () => ({ width: bar.width });
  bar.animations = [];
  bar.animate = (keyframes, options) => {
    const animation = {
      keyframes,
      options,
      cancelled: false,
      cancel: () => {
        animation.cancelled = true;
        animation.abort(new Error("AbortError")); // a cancelled animation rejects `finished`, as in a browser
      },
    };
    animation.finished = new Promise((resolve, reject) => {
      animation.finish = resolve;
      animation.abort = reject;
    });
    bar.animations.push(animation);
    return animation;
  };
  return bar;
}

test("a bar grows from its old size to the new one, only when its size changed", async () => {
  const world = fakeWorld({ css: { "--ds-motion-duration-fill": "600ms", "--ds-motion-easing": "cubic-bezier(0.1, 0.2, 0.3, 0.4)" } });
  const api = motion.createMotion(world.window);
  const grows = fakeBar(100);
  const same = fakeBar(80);
  world.dom.grow = [grows, same];
  let done = false;

  const promise = api.update(() => (grows.width = 160)).then(() => (done = true));
  await later();

  assert.equal(same.animations.length, 0);
  assert.equal(grows.animations.length, 1);
  const { keyframes, options } = grows.animations[0];
  // Shown 100/160 of its new size, then released; added to any transform the bar already has.
  assert.deepEqual(
    keyframes.map((k) => k.transform),
    ["translateX(-30px) scaleX(0.625)", "translateX(0px) scaleX(1)"],
  );
  // Composition adds transform-origin too, so the keyframes leave it alone and the translate keeps the left edge put.
  assert.ok(keyframes.every((k) => !("transformOrigin" in k)));
  assert.equal(options.duration, 600);
  assert.equal(options.easing, "cubic-bezier(0.1,0.2,0.3,0.4)");
  assert.equal(options.composite, "add");
  assert.equal(done, false); // update waits for the bar
  grows.animations[0].finish();
  await promise;
  assert.equal(done, true);
});

test("remove folds the element away, then calls done; reduced motion calls done at once", async () => {
  const world = fakeWorld({ css: { "--ds-motion-duration-default": "280ms", "--ds-motion-easing": "cubic-bezier(0.1, 0.2, 0.3, 0.4)" } });
  const api = motion.createMotion(world.window);
  const item = fakeBar(0);
  item.height = "120px";
  const calls = [];

  api.remove(item, () => calls.push("done"));

  assert.deepEqual(calls, []); // not until the fold has finished
  assert.equal(item.animations.length, 1);
  const { keyframes, options, cancelled } = item.animations[0];
  assert.equal(keyframes[0].height, "120px"); // from its own height...
  assert.equal(keyframes.at(-1).height, "0px"); // ...to nothing, so the items below slide up as it goes
  assert.equal(keyframes.at(-1).opacity, 0);
  assert.equal(options.duration, 280);
  assert.equal(options.easing, "cubic-bezier(0.1,0.2,0.3,0.4)");
  assert.equal(options.fill, "forwards"); // stays folded until done has taken it out
  assert.equal(cancelled, false);
  item.animations[0].finish();
  await later();
  assert.deepEqual(calls, ["done"]);
  assert.equal(item.animations[0].cancelled, true); // released once done has run, so nothing stays folded

  const quiet = fakeWorld({ reduced: true });
  const still = fakeBar(0);
  still.height = "120px";
  motion.createMotion(quiet.window).remove(still, () => calls.push("done at once"));
  assert.deepEqual(calls, ["done", "done at once"]); // already called, nothing awaited
  assert.equal(still.animations.length, 0);
});

test("enter lets a container's children arrive in a short top-to-bottom ripple", async () => {
  const css = { "--ds-motion-duration-fast": "140ms", "--ds-motion-duration-sheet": "420ms" };
  const world = fakeWorld({ css });
  const api = motion.createMotion(world.window);
  const container = fakeElement({ "data-ds-stagger": "" });
  container.children = [fakeBar(0), fakeBar(0), fakeBar(0)];
  world.dom.stagger = [container];

  const finished = api.enter(); // no argument: every [data-ds-stagger] container
  const [a, b, c] = container.children.map((child) => child.animations[0]);

  assert.deepEqual(a.keyframes.map((k) => k.opacity), [0, "1"]); // up to its own opacity
  assert.deepEqual([a.options.delay, b.options.delay, c.options.delay], [0, 140, 280]); // one fast step apart
  assert.equal(a.options.duration, 420);
  assert.equal(a.options.fill, "backwards"); // hidden until its turn
  for (const animation of [a, b, c]) animation.finish();
  await finished;

  // A long list never takes more than 400 ms to start the last item.
  const many = fakeElement();
  many.children = Array.from({ length: 41 }, () => fakeBar(0));
  api.enter(many);
  assert.equal(many.children[40].animations[0].options.delay, 400);
  assert.equal(many.children[1].animations[0].options.delay, 10);

  const quiet = fakeWorld({ css, reduced: true });
  const still = fakeElement();
  still.children = [fakeBar(0)];
  await motion.createMotion(quiet.window).enter(still);
  assert.equal(still.children[0].animations.length, 0);
});

test("the glide rules arrive as a constructed style sheet, timed from the --ds- properties", () => {
  const world = fakeWorld();

  motion.createMotion(world.window);

  assert.equal(world.document.adoptedStyleSheets.length, 1);
  const { text } = world.document.adoptedStyleSheets[0];
  assert.match(text, /::view-transition-group\(\*\.ds-item\)/);
  assert.match(text, /animation-duration:\s*var\(--ds-motion-duration-default/);
  assert.match(text, /animation-timing-function:\s*var\(--ds-motion-easing/);
  assert.equal(world.document.createElement, undefined); // never an inline <style>, so a strict CSP is fine
});

test("the root's group is timed too, so a 0ms default leaves no root animation running", () => {
  const world = fakeWorld();

  motion.createMotion(world.window);

  const { text } = world.document.adoptedStyleSheets[0];
  const timed = "\\{animation-duration:var\\(--ds-motion-duration-default,280ms\\);animation-timing-function:var\\(--ds-motion-easing,";
  for (const part of ["group", "old", "new"]) {
    // Each root pseudo-element sits in a selector list followed by the timed block.
    assert.match(text, new RegExp("::view-transition-" + part + "\\(root\\)[^{}]*" + timed), part);
  }
});

test("a browser without adoptedStyleSheets skips the rules and still works", async () => {
  const world = fakeWorld();
  delete world.document.adoptedStyleSheets;
  const api = motion.createMotion(world.window);
  let runs = 0;

  await api.update(() => runs++);

  assert.equal(runs, 1);
});

test("overlapping updates keep the names until the last transition has finished", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const item = fakeElement({ "data-ds-key": "a" });
  world.dom.key = [item];

  const first = api.update(() => {});
  const second = api.update(() => {});
  await first;

  assert.equal(item.style.values.get("view-transition-name"), "ds-a"); // the second still needs it
  await second;
  assert.equal(item.style.values.size, 0);
});

test("two items with one key: fn runs plainly, with no transition", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  world.dom.key = [fakeElement({ "data-ds-key": "a" }), fakeElement({ "data-ds-key": "a" })];
  let runs = 0;

  await api.update(() => runs++);

  assert.equal(runs, 1);
  assert.deepEqual(world.log, []); // a duplicate name would abort the transition
  for (const element of world.dom.key) assert.equal(element.style.values.size, 0); // and nothing is left behind
});

test("without view transitions, fn runs once at once, then tickers roll and bars grow", async () => {
  const world = fakeWorld({ transitions: false, css: { "--ds-motion-duration-fill": "600ms" } });
  const api = motion.createMotion(world.window);
  const ticker = fakeElement({}, { textContent: "of 30 this hour" });
  const bar = fakeBar(100);
  world.dom.ticker = [ticker];
  world.dom.grow = [bar];
  let runs = 0;

  const promise = api.update(() => {
    runs++;
    ticker.textContent = "of 40 this hour";
    bar.width = 160;
  });

  assert.equal(runs, 1); // fn has already run
  assert.equal(ticker.textContent, "of 30 this hour"); // and the number starts rolling from the old one
  assert.equal(bar.animations.length, 1);
  assert.deepEqual(bar.animations[0].keyframes.map((k) => k.transform), ["translateX(-30px) scaleX(0.625)", "translateX(0px) scaleX(1)"]);
  frame(world, 1000);
  frame(world, 1700);
  bar.animations[0].finish();
  await promise;
  assert.equal(ticker.textContent, "of 40 this hour");
  assert.equal(runs, 1);
});

test("without view transitions, remove folds and enter ripples", () => {
  const world = fakeWorld({ transitions: false });
  const api = motion.createMotion(world.window);
  const item = fakeBar(0);
  item.height = "50px";
  const parent = fakeElement();
  parent.children = [fakeBar(0), fakeBar(0)];

  api.remove(item, () => {});
  api.enter(parent);

  assert.equal(item.animations.length, 1);
  assert.deepEqual(parent.children.map((child) => child.animations.length), [1, 1]);
});

test("reduced motion turns everything off, and fn still runs once", async () => {
  const world = fakeWorld({ reduced: true });
  const api = motion.createMotion(world.window);
  const ticker = fakeElement({}, { textContent: "of 30 this hour" });
  const bar = fakeBar(100);
  const parent = fakeElement();
  parent.children = [fakeBar(0)];
  const folding = fakeBar(0);
  folding.height = "50px";
  world.dom.ticker = [ticker];
  world.dom.grow = [bar];
  let runs = 0;
  let done = 0;

  await api.update(() => {
    runs++;
    ticker.textContent = "of 40 this hour";
    bar.width = 160;
  });
  await api.enter(parent);
  api.remove(folding, () => done++);

  assert.equal(runs, 1);
  assert.equal(ticker.textContent, "of 40 this hour"); // straight to the new value
  assert.equal(world.frames.length, 0);
  assert.equal(bar.animations.length + parent.children[0].animations.length + folding.animations.length, 0);
  assert.equal(done, 1);
});

test("a ticker marked with an id rolls even when the page re-renders it as a new element", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const before = fakeElement({ "data-ds-ticker": "limit" }, { textContent: "of 30 this hour" });
  const other = fakeElement({ "data-ds-ticker": "paused" }, { textContent: "2 paused" });
  const after = fakeElement({ "data-ds-ticker": "limit" }, { textContent: "of 40 this hour" });
  const otherAfter = fakeElement({ "data-ds-ticker": "paused" }, { textContent: "2 paused" });
  world.dom.ticker = [before, other];

  const promise = api.update(() => (world.dom.ticker = [otherAfter, after])); // innerHTML-style re-render
  await later();

  assert.equal(after.textContent, "of 30 this hour"); // the new element starts from the old number
  assert.equal(otherAfter.textContent, "2 paused");
  frame(world, 1000);
  frame(world, 1700);
  await promise;
  assert.equal(after.textContent, "of 40 this hour");
});

test("a ticker without an id is matched by element, so a replaced element just shows the new text", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const before = fakeElement({}, { textContent: "of 30 this hour" });
  const after = fakeElement({}, { textContent: "of 40 this hour" });
  world.dom.ticker = [before];

  await api.update(() => (world.dom.ticker = [after]));

  assert.equal(after.textContent, "of 40 this hour");
  assert.equal(world.frames.length, 0);
});

test("a bar marked with an id grows from the old size even when it is a new element", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const before = fakeBar(100);
  before.attrs["data-ds-grow"] = "usage";
  const after = fakeBar(160);
  after.attrs["data-ds-grow"] = "usage";
  world.dom.grow = [before];

  const promise = api.update(() => (world.dom.grow = [after]));
  await later();

  assert.equal(before.animations.length, 0);
  assert.equal(after.animations.length, 1);
  assert.equal(after.animations[0].keyframes[0].transform, "translateX(-30px) scaleX(0.625)");
  after.animations[0].finish();
  await promise;
});

test("a ticker element reused for another record does not roll from the old record's number", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  // The page keeps this element and gives it another record: "a" showed 30, "b" shows 80.
  const reused = fakeElement({ "data-ds-ticker": "a" }, { textContent: "of 30 this hour" });
  world.dom.ticker = [reused];

  const promise = api.update(() => {
    reused.attrs["data-ds-ticker"] = "b";
    reused.textContent = "of 80 this hour";
  });
  await later();

  assert.equal(reused.textContent, "of 80 this hour"); // settled at the new value, not on its way from 30
  assert.equal(world.frames.length, 0);
  await promise;
});

test("a bar element reused for another record does not grow from the old record's size", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  // The page keeps this element and gives it another record: "a" was 100 wide, "b" is 160.
  const reused = fakeBar(100);
  reused.attrs["data-ds-grow"] = "a";
  world.dom.grow = [reused];

  const promise = api.update(() => {
    reused.attrs["data-ds-grow"] = "b";
    reused.width = 160;
  });
  await later();

  assert.equal(reused.animations.length, 0); // it just shows its new size
  await promise;
});

test("an element that had no id and gets one in the change is not rolled either", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const ticker = fakeElement({}, { textContent: "of 30 this hour" });
  world.dom.ticker = [ticker];

  const promise = api.update(() => {
    ticker.attrs["data-ds-ticker"] = "b";
    ticker.textContent = "of 80 this hour";
  });
  await later();

  assert.equal(ticker.textContent, "of 80 this hour");
  assert.equal(world.frames.length, 0);
  await promise;
});

test("two reused elements that swap ids roll nothing: each id still shows its own number", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const first = fakeElement({ "data-ds-ticker": "a" }, { textContent: "10 sent" });
  const second = fakeElement({ "data-ds-ticker": "b" }, { textContent: "20 sent" });
  world.dom.ticker = [first, second];

  const promise = api.update(() => {
    first.attrs["data-ds-ticker"] = "b";
    first.textContent = "20 sent";
    second.attrs["data-ds-ticker"] = "a";
    second.textContent = "10 sent";
  });
  await later();

  assert.equal(first.textContent, "20 sent");
  assert.equal(second.textContent, "10 sent");
  assert.equal(world.frames.length, 0);
  await promise;
});

test("an update that falls back to plain fn and waits behind a transition gives no animation once reduced motion is on", async () => {
  const world = fakeWorld({ css: { "--ds-motion-duration-fill": "600ms" } });
  const api = motion.createMotion(world.window);
  const ticker = fakeElement({}, { textContent: "of 30 this hour" });
  const bar = fakeBar(100);
  world.dom.ticker = [ticker];
  world.dom.grow = [bar];
  world.dom.key = [fakeElement({ "data-ds-key": "a" })];

  // The first update starts a transition whose callback has not run yet.
  const first = api.update(() => {});
  // The second finds duplicate keys: it falls back to plain fn and queues behind the first.
  world.dom.key = [fakeElement({ "data-ds-key": "a" }), fakeElement({ "data-ds-key": "a" })];
  const second = api.update(() => {
    ticker.textContent = "of 40 this hour";
    bar.width = 160;
  });
  world.media.change(true); // reduced motion goes on while the second still waits
  await later();

  assert.equal(ticker.textContent, "of 40 this hour"); // straight to the new value
  assert.equal(world.frames.length, 0);
  assert.equal(bar.animations.length, 0);
  await Promise.all([first, second]);
});

test("the same queued fallback update still animates while motion stays allowed", async () => {
  const world = fakeWorld({ css: { "--ds-motion-duration-fill": "600ms" } });
  const api = motion.createMotion(world.window);
  const bar = fakeBar(100);
  world.dom.grow = [bar];
  world.dom.key = [fakeElement({ "data-ds-key": "a" })];

  const first = api.update(() => {});
  world.dom.key = [fakeElement({ "data-ds-key": "a" }), fakeElement({ "data-ds-key": "a" })];
  const second = api.update(() => (bar.width = 160));
  await later();

  assert.equal(bar.animations.length, 1);
  bar.animations[0].finish();
  await Promise.all([first, second]);
});

test("a minus sign right before the digits is part of a ticker's number", () => {
  assert.equal(motion.readNumber("-5 left"), -5);
  assert.equal(motion.tickerText("-6 left", 4, -6, 0.5), "-1 left");
  assert.equal(motion.tickerText("-6 left", 4, -6, 0.2), "2 left");
  assert.equal(motion.tickerText("-1,200 net", 0, -1200, 0.5), "-600 net");
});

test("the root crossfade uses the same timing, in a rule of its own", () => {
  const world = fakeWorld();

  motion.createMotion(world.window);

  const rules = world.document.adoptedStyleSheets[0].text.split("}").filter(Boolean);
  const root = rules.find((rule) => rule.includes("(root)"));
  assert.ok(root, "a rule for the root");
  assert.match(root, /::view-transition-old\(root\)/);
  assert.match(root, /::view-transition-new\(root\)/);
  assert.match(root, /animation-duration:\s*var\(--ds-motion-duration-default/);
  assert.match(root, /animation-timing-function:\s*var\(--ds-motion-easing/);
  assert.ok(!root.includes("ds-item")); // separate, so a browser without view-transition-class still gets it
});


test("overlapping updates: the last fn's value wins and an earlier roll or grow never writes over it", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const ticker = fakeElement({}, { textContent: "of 30 this hour" });
  const bar = fakeBar(100);
  world.dom.ticker = [ticker];
  world.dom.grow = [bar];

  // Both are called before the browser runs either callback: both snapshot 30 and 100.
  const first = api.update(() => {
    ticker.textContent = "of 40 this hour";
    bar.width = 160;
  });
  const second = api.update(() => {
    ticker.textContent = "of 30 this hour";
    bar.width = 100;
  });
  await later();
  for (let time = 1000; time <= 3000; time += 300) frame(world, time);
  bar.animations.forEach((animation) => animation.finish());
  await Promise.all([first, second]);

  assert.equal(ticker.textContent, "of 30 this hour"); // not the first update's 40
  assert.equal(world.frames.length, 0);
  assert.equal(bar.animations[0].cancelled, true);
});

function rollingWorld() {
  const world = fakeWorld({ css: { "--ds-motion-duration-fill": "600ms" } });
  const api = motion.createMotion(world.window);
  const ticker = fakeElement({}, { textContent: "of 30 this hour" });
  const bar = fakeBar(100);
  world.dom.ticker = [ticker];
  world.dom.grow = [bar];
  const promise = api.update(() => {
    ticker.textContent = "of 40 this hour";
    bar.width = 160;
  });
  return { world, api, ticker, bar, promise };
}

test("turning reduced motion on stops every running animation, and fold still calls done", async () => {
  const { world, api, ticker, bar, promise } = rollingWorld();
  await later();
  frame(world, 1000);
  frame(world, 1100); // mid-roll
  const parent = fakeElement();
  parent.children = [fakeBar(0), fakeBar(0)];
  api.enter(parent);
  const folding = fakeBar(0);
  folding.height = "50px";
  let done = 0;
  api.remove(folding, () => done++);

  world.media.change(true);

  assert.equal(ticker.textContent, "of 40 this hour"); // its final text, not a number on the way
  assert.equal(world.frames.length, 0);
  assert.equal(bar.animations[0].cancelled, true);
  assert.deepEqual(parent.children.map((child) => child.animations[0].cancelled), [true, true]);
  assert.equal(folding.animations[0].cancelled, true);
  assert.equal(done, 1); // the element is still taken out
  await promise;
});

test("an update under reduced motion first stops what is still running, so nothing overwrites it", async () => {
  const { world, api, ticker, bar, promise } = rollingWorld();
  await later();
  frame(world, 1000);
  frame(world, 1100);
  world.media.matches = true; // the preference changed without a change event reaching us

  await api.update(() => (ticker.textContent = "of 50 this hour"));
  frame(world, 1200);
  frame(world, 1800);

  assert.equal(ticker.textContent, "of 50 this hour");
  assert.equal(world.frames.length, 0);
  assert.equal(bar.animations[0].cancelled, true);
  await promise;
});

test("a curve with an unreadable coordinate falls back in the timing handed to animations too", async () => {
  const world = fakeWorld({ css: { "--ds-motion-duration-fill": "600ms", "--ds-motion-easing": "cubic-bezier(0, -, 1, 1)" } });
  const api = motion.createMotion(world.window);
  const bar = fakeBar(100);
  world.dom.grow = [bar];

  const promise = api.update(() => (bar.width = 160));
  await later();

  assert.equal(bar.animations[0].options.easing, "cubic-bezier(0.32,0.72,0,1)"); // never "NaN"
  bar.animations[0].finish();
  await promise;
});

test("a bar keeps its left edge in place whatever its transform origin", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const centred = fakeBar(100);
  const anchored = fakeBar(100);
  anchored.origin = "0px 5px"; // the app already scales this one from its left edge
  world.dom.grow = [centred, anchored];

  const promise = api.update(() => {
    centred.width = 160;
    anchored.width = 160;
  });
  await later();

  // Scaling about the centre (80px) by 0.625 would leave the bar 30px too far right: move it back by 30px.
  assert.deepEqual(centred.animations[0].keyframes.map((k) => k.transform), ["translateX(-30px) scaleX(0.625)", "translateX(0px) scaleX(1)"]);
  assert.equal(anchored.animations[0].keyframes[0].transform, "translateX(0px) scaleX(0.625)");
  assert.equal(centred.animations[0].options.composite, "add");
  for (const bar of [centred, anchored]) bar.animations[0].finish();
  await promise;
});

test("enter reads how far children rise and how long the ripple may take from the motion tokens", () => {
  // F12: --ds-motion-stagger-offset (12px) and --ds-motion-duration-stagger (400ms) are the entrance's own values.
  const css = {
    "--ds-motion-duration-fast": "140ms",
    "--ds-motion-duration-sheet": "420ms",
    "--ds-motion-stagger-offset": "20px",
    "--ds-motion-duration-stagger": "200ms",
  };
  const api = motion.createMotion(fakeWorld({ css }).window);
  const parent = fakeElement();
  parent.children = Array.from({ length: 41 }, () => fakeBar(0));

  api.enter(parent);

  assert.deepEqual(parent.children[0].animations[0].keyframes.map((k) => k.transform), ["translateY(20px)", "none"]);
  assert.equal(parent.children[40].animations[0].options.delay, 200); // the last child starts within the token's 200ms
  assert.equal(parent.children[1].animations[0].options.delay, 5);
});

test("enter falls back to today's 12px and 400ms when the tokens are missing or unreadable", () => {
  for (const css of [{}, { "--ds-motion-stagger-offset": "far", "--ds-motion-duration-stagger": "soon" }]) {
    const api = motion.createMotion(fakeWorld({ css: { "--ds-motion-duration-fast": "140ms", "--ds-motion-duration-sheet": "420ms", ...css } }).window);
    const parent = fakeElement();
    parent.children = Array.from({ length: 41 }, () => fakeBar(0));

    api.enter(parent);

    assert.deepEqual(parent.children[0].animations[0].keyframes.map((k) => k.transform), ["translateY(12px)", "none"]);
    assert.equal(parent.children[40].animations[0].options.delay, 400);
  }
});

test("enter keeps a child's own transform and opacity", () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const parent = fakeElement();
  const placed = fakeBar(0);
  placed.transform = "matrix(1, 0, 0, 1, -50, 0)"; // e.g. translateX(-50%) positioning
  placed.opacity = "0.5";
  const plain = fakeBar(0);
  parent.children = [placed, plain];

  api.enter(parent);

  const [from, to] = placed.animations[0].keyframes;
  assert.deepEqual([from.opacity, to.opacity], [0, "0.5"]);
  assert.equal(from.transform, "matrix(1, 0, 0, 1, -50, 0) translateY(12px)"); // rises from below its own position
  assert.equal(to.transform, "matrix(1, 0, 0, 1, -50, 0)"); // and lands exactly on it, so nothing snaps
  assert.deepEqual(plain.animations[0].keyframes.map((k) => k.transform), ["translateY(12px)", "none"]);
});

test("reduced motion switched on while a transition is pending: that callback starts nothing and order is kept", async () => {
  const world = fakeWorld({ css: { "--ds-motion-duration-fill": "600ms" } });
  const api = motion.createMotion(world.window);
  const ticker = fakeElement({}, { textContent: "of 30 this hour" });
  const bar = fakeBar(100);
  world.dom.ticker = [ticker];
  world.dom.grow = [bar];
  const order = [];
  const skipped = [];
  const start = world.document.startViewTransition;
  world.document.startViewTransition = (callback) => {
    const transition = start(callback);
    transition.skipTransition = () => skipped.push(true);
    return transition;
  };

  const first = api.update(() => {
    order.push("A");
    ticker.textContent = "of 40 this hour";
    bar.width = 160;
  });
  world.media.change(true); // the preference turns on before A's callback has run
  assert.equal(skipped.length, 1); // the pending transition is skipped
  const second = api.update(() => {
    order.push("B");
    ticker.textContent = "of 50 this hour";
  });

  assert.deepEqual(order, []); // B waits for A's callback, so A can never overwrite B
  await Promise.all([first, second]);
  assert.deepEqual(order, ["A", "B"]);
  assert.equal(ticker.textContent, "of 50 this hour");
  assert.equal(world.frames.length, 0); // no roll started
  assert.equal(bar.animations.length, 0); // no grow started
});

test("an update made from inside a pending callback still waits for the reduced-motion update queued before it", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const state = { value: "start" };
  const order = [];
  let third;

  const first = api.update(() => {
    order.push("A");
    state.value = "A";
    queueMicrotask(() => {
      third = api.update(() => {
        order.push("C");
        state.value = "C"; // the newest state, made last
      });
    });
  });
  world.media.change(true); // A's callback is still pending
  const second = api.update(() => {
    order.push("B");
    state.value = "B";
  });

  assert.deepEqual(order, []); // B waits for A
  await Promise.all([first, second]);
  await third;
  assert.deepEqual(order, ["A", "B", "C"]); // C came after B, so it runs after B
  assert.equal(state.value, "C"); // B never overwrites C's newer state

  let runs = 0;
  const fourth = api.update(() => runs++);
  assert.equal(runs, 1); // nothing pending any more: synchronous again
  await fourth;
});

test("under reduced motion an update with nothing pending runs fn at once, and an error in the earlier one does not block it", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const boom = new Error("boom");
  const first = api.update(() => {
    throw boom;
  });
  world.media.change(true);
  const order = [];
  const second = api.update(() => order.push("B"));

  await assert.rejects(first, boom);
  await second;
  assert.deepEqual(order, ["B"]);

  let runs = 0;
  const third = api.update(() => runs++);
  assert.equal(runs, 1); // nothing pending: synchronous again
  await third;
});

test("timing is read from the element being animated, so a local override applies", async () => {
  const world = fakeWorld({
    css: { "--ds-motion-duration-fill": "600ms", "--ds-motion-duration-default": "280ms", "--ds-motion-duration-sheet": "420ms" },
  });
  const api = motion.createMotion(world.window);
  const slow = fakeBar(100);
  slow.css = { "--ds-motion-duration-fill": "1000ms", "--ds-motion-easing": "cubic-bezier(0.1, 0.2, 0.3, 0.4)" };
  const off = fakeBar(100);
  off.css = { "--ds-motion-duration-fill": "0ms" }; // a container with motion switched off
  const offTicker = fakeElement({}, { textContent: "of 30 this hour", css: { "--ds-motion-duration-fill": "0ms" } });
  world.dom.grow = [slow, off];
  world.dom.ticker = [offTicker];

  const promise = api.update(() => {
    slow.width = off.width = 160;
    offTicker.textContent = "of 40 this hour";
  });
  await later();

  assert.equal(slow.animations[0].options.duration, 1000);
  assert.equal(slow.animations[0].options.easing, "cubic-bezier(0.1,0.2,0.3,0.4)");
  assert.equal(off.animations.length, 0); // 0ms on the element means no animation for it
  assert.equal(offTicker.textContent, "of 40 this hour"); // straight to the new value
  assert.equal(world.frames.length, 0);
  slow.animations[0].finish();
  await promise;

  const folding = fakeBar(0);
  folding.height = "50px";
  folding.css = { "--ds-motion-duration-default": "500ms" };
  const still = fakeBar(0);
  still.height = "50px";
  still.css = { "--ds-motion-duration-default": "0ms" };
  let done = 0;
  api.remove(folding, () => done++);
  api.remove(still, () => done++);
  assert.equal(folding.animations[0].options.duration, 500);
  assert.equal(still.animations.length, 0);
  assert.equal(done, 1); // the 0ms one is taken out at once

  const parent = fakeElement();
  const quick = fakeBar(0);
  quick.css = { "--ds-motion-duration-sheet": "100ms" };
  const none = fakeBar(0);
  none.css = { "--ds-motion-duration-sheet": "0ms" };
  parent.children = [quick, none];
  api.enter(parent);
  assert.equal(quick.animations[0].options.duration, 100);
  assert.equal(none.animations.length, 0);
});

test("a bar that shrinks to nothing animates its width from the old size", async () => {
  const world = fakeWorld();
  const api = motion.createMotion(world.window);
  const bar = fakeBar(100);
  world.dom.grow = [bar];

  const promise = api.update(() => (bar.width = 0)); // e.g. a usage bar going to 0%
  await later();

  assert.equal(bar.animations.length, 1);
  assert.deepEqual(bar.animations[0].keyframes, [{ width: "100px" }, { width: "0px" }]);
  assert.notEqual(bar.animations[0].options.composite, "add"); // width is replaced, not added
  bar.animations[0].finish();
  await promise;

  const gone = fakeBar(100);
  gone.isConnected = false; // a bar taken out of the page is left alone
  world.dom.grow = [gone];
  await api.update(() => (gone.width = 0));
  assert.equal(gone.animations.length, 0);
});

test("remove takes minimum height, borders, padding and margins to nothing too", () => {
  const api = motion.createMotion(fakeWorld().window);
  const item = fakeBar(0);
  item.height = "80px";

  api.remove(item, () => {});

  const end = item.animations[0].keyframes.at(-1);
  for (const property of ["height", "minHeight", "marginTop", "marginBottom", "paddingTop", "paddingBottom", "borderTopWidth", "borderBottomWidth"]) {
    assert.equal(end[property], "0px", property); // else a minimum height would hold the space until done
  }
});

test("enter called again mid-stagger cancels the first ripple and lands every child on its own style", async () => {
  const world = fakeWorld({ css: { "--ds-motion-duration-fast": "140ms", "--ds-motion-duration-sheet": "420ms" } });
  // As a browser does: while an entrance is running, the computed style is the animation's, and a child still
  // waiting for its turn (fill: backwards) shows the first keyframe, hidden and lowered.
  const own = world.window.getComputedStyle;
  world.window.getComputedStyle = (element) => {
    const style = own(element);
    const running = (element.animations || []).filter((a) => !a.cancelled).at(-1);
    return running ? { ...style, opacity: String(running.keyframes[0].opacity), transform: running.keyframes[0].transform } : style;
  };
  const api = motion.createMotion(world.window);
  const parent = fakeElement();
  const placed = fakeBar(0);
  placed.transform = "matrix(1, 0, 0, 1, -50, 0)";
  placed.opacity = "0.5";
  parent.children = [fakeBar(0), placed, fakeBar(0)];

  const first = api.enter(parent);
  api.enter(parent); // e.g. the list re-rendered while the ripple was still going

  for (const child of parent.children) {
    assert.equal(child.animations.length, 2);
    assert.equal(child.animations[0].cancelled, true); // the first ripple is stopped
  }
  const [from, to] = placed.animations[1].keyframes;
  assert.deepEqual([from.opacity, to.opacity], [0, "0.5"]); // lands on its own opacity, not the waiting 0
  assert.equal(from.transform, "matrix(1, 0, 0, 1, -50, 0) translateY(12px)");
  assert.equal(to.transform, "matrix(1, 0, 0, 1, -50, 0)");
  for (const child of [parent.children[0], parent.children[2]]) {
    assert.deepEqual(child.animations[1].keyframes.map((k) => [k.opacity, k.transform]), [[0, "translateY(12px)"], ["1", "none"]]);
  }
  await first; // the cancelled ripple still settles
});
