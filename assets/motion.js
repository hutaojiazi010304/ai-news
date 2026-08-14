(function () {
  if (!window.gsap) return;

  const mm = gsap.matchMedia();

  mm.add("(prefers-reduced-motion: no-preference)", function () {
    gsap.defaults({ duration: 0.55, ease: "power3.out" });

    // Intro / list entry animations intentionally live in styles.css as
    // transform-only @keyframes: any JS "from" tween that touches opacity
    // strands elements invisible when the tab is hidden mid-flight, because
    // the GSAP ticker freezes while rendering is paused.

    // Section scroll reveal via IntersectionObserver. Keep sections visible:
    // hiding whole content blocks can leave blank viewports after responsive
    // reflow or rapid mobile scrolling.
    const revealEls = document.querySelectorAll(".waytoagi-wrap, .list-wrap");
    if (revealEls.length && window.IntersectionObserver) {
      gsap.set(revealEls, { y: 14 });
      const observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            gsap.to(entry.target, { y: 0, duration: 0.45, clearProps: "transform" });
            observer.unobserve(entry.target);
          }
        });
      }, { threshold: 0.08 });
      revealEls.forEach(function (el) { observer.observe(el); });
    }
  });
}());
