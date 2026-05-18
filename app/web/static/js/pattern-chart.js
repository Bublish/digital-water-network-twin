// Pattern chart — uPlot line of the 96-step demand-multiplier curve with a
// cursor marker tracking the current 15-min step.
//
// createPatternChart(el) -> { setPattern(multipliers), setCursor(stepIndex), reTheme(), destroy() }
//
// Series 0: the curve (96 points, x = step index 0..95).
// Series 1: the cursor — a single highlighted point we redraw by replacing
// only its y array (faster than calling setData() each tick).

(function () {
  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    return {
      accent:     cs.getPropertyValue("--accent").trim()     || "#0891b2",
      text:       cs.getPropertyValue("--text").trim()       || "#1f2933",
      muted:      cs.getPropertyValue("--text-muted").trim() || "#64748b",
      border:     cs.getPropertyValue("--border").trim()     || "#e2e8f0",
      surface2:   cs.getPropertyValue("--surface-2").trim()  || "#f8fafc",
    };
  }

  function makeOpts(width, height, colors) {
    const xs = Array.from({ length: 96 }, (_, i) => i);
    return {
      width: width,
      height: height,
      legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: false } },
      scales: {
        x: { time: false, range: [0, 95] },
        y: { range: (u, dmin, dmax) => [Math.min(0.5, dmin), Math.max(1.5, dmax)] },
      },
      axes: [
        {
          stroke: colors.muted,
          grid:   { stroke: colors.border, width: 1 },
          ticks:  { stroke: colors.border, width: 1 },
          values: (u, splits) => splits.map(s => {
            // step index -> hh:mm; 4 steps per hour
            const h = Math.floor(s / 4);
            const m = (s % 4) * 15;
            return (h === 0 || h === 24)
              ? "00:00"
              : (h.toString().padStart(2, "0") + ":" + m.toString().padStart(2, "0"));
          }),
          space: 60,
        },
        {
          stroke: colors.muted,
          grid:   { stroke: colors.border, width: 1 },
          ticks:  { stroke: colors.border, width: 1 },
          size: 36,
        },
      ],
      series: [
        { label: "step" },
        {
          label: "multiplier",
          stroke: colors.accent,
          width: 2,
          fill: colors.accent + "1f", // ~12% alpha
          points: { show: false },
        },
        {
          label: "cursor",
          stroke: colors.accent,
          width: 0,
          points: {
            show: true,
            size: 9,
            stroke: colors.accent,
            fill: colors.surface2,
            width: 2,
          },
        },
      ],
    };
  }

  window.createPatternChart = function createPatternChart(el) {
    if (!el || typeof uPlot !== "function") {
      return { setPattern() {}, setCursor() {}, reTheme() {}, destroy() {} };
    }

    let multipliers = new Array(96).fill(null);
    let cursorIdx = null;

    function cursorYs() {
      const ys = new Array(96).fill(null);
      if (cursorIdx != null && multipliers[cursorIdx] != null) {
        ys[cursorIdx] = multipliers[cursorIdx];
      }
      return ys;
    }

    let colors = readColors();
    const rect = el.getBoundingClientRect();
    const width = Math.max(200, Math.floor(rect.width));
    const height = Math.max(140, Math.floor(rect.height || 160));

    const xs = Array.from({ length: 96 }, (_, i) => i);
    let plot = new uPlot(makeOpts(width, height, colors), [xs, multipliers, cursorYs()], el);

    // Resize on container width change
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const w = Math.max(200, Math.floor(entry.contentRect.width));
        const h = Math.max(140, Math.floor(entry.contentRect.height || height));
        plot.setSize({ width: w, height: h });
      }
    });
    ro.observe(el);

    function setData() {
      plot.setData([xs, multipliers, cursorYs()]);
    }

    function rebuild() {
      const r = el.getBoundingClientRect();
      const w = Math.max(200, Math.floor(r.width));
      const h = Math.max(140, Math.floor(r.height || height));
      plot.destroy();
      colors = readColors();
      plot = new uPlot(makeOpts(w, h, colors), [xs, multipliers, cursorYs()], el);
    }

    return {
      setPattern(arr) {
        multipliers = (Array.isArray(arr) && arr.length === 96)
          ? arr.map(v => Number(v))
          : new Array(96).fill(null);
        setData();
      },
      setCursor(idx) {
        if (idx === cursorIdx) return;
        cursorIdx = (idx == null || isNaN(idx)) ? null : Math.max(0, Math.min(95, idx | 0));
        setData();
      },
      reTheme() { rebuild(); },
      destroy() { ro.disconnect(); plot.destroy(); },
    };
  };
})();
