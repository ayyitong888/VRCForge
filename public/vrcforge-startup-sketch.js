(function drawStartupSelfPortrait() {
  const shell = document.querySelector("main[data-vrcforge-startup-shell]");
  if (!shell || shell.hidden) return;
  const canvas = shell.querySelector(".startup-sketch");
  const context = canvas.getContext("2d");
  if (!context) return;

  const timeline = shell.getAnimations().find((animation) => animation.animationName === "startup-camera");
  const width = canvas.width;
  const height = canvas.height;
  const colorStart = 2000;
  const colorEnd = 3000;
  const clamp = (value) => Math.max(0, Math.min(1, value));
  const smooth = (value) => value * value * (3 - 2 * value);

  // These regions are traced in the original 1040 × 802 image coordinates.
  const headset = new Path2D("M358 111 Q395 89 450 92 L464 100 Q438 123 416 198 L405 204 Q374 167 358 111 Z M496 76 Q526 51 560 48 Q586 17 609 0 H632 Q650 29 660 83 Q685 98 703 139 Q727 131 747 159 Q764 194 750 234 Q743 249 723 252 Q709 278 680 299 L655 316 Q643 328 628 323 L629 309 L650 298 Q679 283 705 250 Q676 230 677 183 Q675 156 686 145 Q665 106 635 90 Q583 46 498 88 Z");
  const face = new Path2D("M484 303 Q509 332 538 339 Q569 351 600 341 Q618 337 631 322 L638 329 Q630 343 602 353 Q569 357 539 351 Q507 342 483 318 Z M548 279 H562 V294 H548 Z M571 297 H604 V317 H571 Z");
  const hair = new Path2D("M395 0 H1040 V737 Q977 755 927 699 Q897 642 900 559 Q894 464 857 380 Q833 316 795 284 Q751 299 710 341 Q673 388 694 438 Q753 463 733 510 Q719 544 667 524 Q683 496 652 466 Q622 433 641 372 Q648 349 630 339 Q614 351 611 366 Q618 410 584 475 Q574 503 540 520 Q523 518 493 512 Q508 478 482 459 Q459 461 452 451 Q481 442 472 409 Q422 396 410 352 Q390 324 399 277 Q399 224 417 179 Z");
  const eyes = new Path2D("M478 281 Q489 265 506 260 Q526 250 543 263 L547 277 Q540 293 519 302 Q499 302 483 292 Z M581 236 Q585 212 607 204 Q628 194 648 196 L652 205 Q644 215 645 231 Q632 251 608 255 Q591 252 581 242 Z");
  const remaining = new Path2D("M0 0 H1040 V802 H0 Z");
  const parts = [
    { shape: headset, start: 0, end: 410 },
    { shape: face, start: 500, end: 940 },
    { shape: hair, start: 1000, end: 1450 },
    { shape: eyes, start: 1510, end: 1750 },
    { shape: remaining, start: 1750, end: 1980 },
  ];
  const subject = new Path2D("M304 297 Q317 293 329 301 L398 351 L395 358 L367 346 Q368 385 378 403 Q408 408 420 484 Q432 507 451 525 Q479 486 475 441 Q457 431 440 395 Q416 360 407 323 Q396 285 409 241 L414 195 Q383 169 362 115 Q392 95 446 94 L468 73 Q449 42 480 24 L500 19 Q473 36 482 59 Q514 45 560 49 Q585 14 610 0 H631 Q649 21 658 81 Q704 99 751 154 Q780 203 824 247 Q888 298 923 343 Q986 379 1040 453 V706 Q1010 719 960 723 Q884 729 808 729 Q765 734 708 727 L461 714 Q409 702 374 677 Q325 635 309 573 Q303 537 284 478 Q271 450 298 426 Q315 413 329 410 Q317 387 303 375 Q282 366 285 347 Q285 325 300 313 Z");

  function surface() {
    const buffer = document.createElement("canvas");
    buffer.width = width;
    buffer.height = height;
    return buffer.getContext("2d");
  }

  async function loadImage(src) {
    const image = new Image();
    image.src = src;
    await image.decode();
    return image;
  }

  void Promise.all([
    loadImage("/vrcforge-startup-registered-ink.webp"),
    loadImage("/vrcforge-startup-portrait.webp"),
  ]).then(([ink, original]) => {
    if (!shell.isConnected) return;
    const layers = parts.map(({ shape }, index) => {
      const layer = surface();
      layer.save();
      layer.clip(shape);
      layer.drawImage(ink, 0, 0);
      layer.restore();
      layer.globalCompositeOperation = "destination-out";
      if (shape !== eyes) layer.fill(eyes);
      for (const previous of parts.slice(0, index)) layer.fill(previous.shape);
      return layer.canvas;
    });

    const subjectMask = surface();
    subjectMask.filter = "blur(5px)";
    subjectMask.fill(subject);
    const character = surface();
    character.drawImage(original, 0, 0);
    character.globalCompositeOperation = "destination-in";
    character.drawImage(subjectMask.canvas, 0, 0);
    const washLayer = surface();
    const centerX = 557;
    const centerY = 279;
    const feather = 140;
    const radius = Math.hypot(Math.max(centerX, width - centerX), height - centerY);
    let previousTime = -1;
    let previousIdle = -1;

    function paintIdle(idle) {
      if (idle <= 0) return;
      const pulse = .05 + .16 * (1 - Math.cos(idle / 3300 * Math.PI * 2)) / 2;
      context.save();
      context.globalCompositeOperation = "screen";
      for (const [x, y, radius] of [[727, 193, 17], [615, 42, 19], [408, 143, 15]]) {
        const light = context.createRadialGradient(x, y, 0, x, y, radius);
        light.addColorStop(0, `rgba(255, 105, 192, ${pulse})`);
        light.addColorStop(1, "transparent");
        context.fillStyle = light;
        context.fillRect(x - radius, y - radius, radius * 2, radius * 2);
      }
      context.restore();
      const beat = (idle + 1450) % 2900;
      if (beat >= 150) return;
      const blink = Math.sin(Math.PI * beat / 150);
      for (const [x, y, angle] of [[517, 278, -.46], [615, 230, -.35]]) {
        context.save();
        context.translate(x, y);
        context.rotate(angle);
        context.scale(1, .79);
        const skin = context.createRadialGradient(0, 0, 14, 0, 0, 26);
        skin.addColorStop(0, "#f6c4cb");
        skin.addColorStop(1, "#f6c4cb00");
        context.globalAlpha = blink;
        context.fillStyle = skin;
        context.fillRect(-26, -26, 52, 52);
        context.beginPath();
        context.moveTo(-17, 1);
        context.quadraticCurveTo(0, 10, 17, 0);
        context.strokeStyle = "#8b3856";
        context.lineWidth = 2.4;
        context.stroke();
        context.restore();
      }
    }

    function paint(time, idle = 0) {
      time = Math.min(time, colorEnd);
      if (time === previousTime && idle === previousIdle) return;
      previousTime = time;
      previousIdle = idle;
      context.clearRect(0, 0, width, height);
      if (time >= colorEnd) {
        context.drawImage(original, 0, 0);
        paintIdle(idle);
        return;
      }
      for (const [index, { start, end }] of parts.entries()) {
        const opacity = smooth(clamp((time - start) / (end - start)));
        if (!opacity) continue;
        context.globalAlpha = opacity;
        context.drawImage(layers[index], 0, 0);
      }
      context.globalAlpha = 1;
      if (time <= colorStart) return;

      const progress = (time - colorStart) / (colorEnd - colorStart);
      context.globalAlpha = smooth(progress);
      context.drawImage(original, 0, 0);
      context.globalAlpha = 1;

      const spread = progress * (radius + feather);
      const wash = washLayer.createRadialGradient(
        centerX, centerY, Math.max(0, spread - feather), centerX, centerY, spread,
      );
      wash.addColorStop(0, `rgba(0, 0, 0, ${clamp(spread / feather)})`);
      wash.addColorStop(1, "transparent");
      washLayer.globalCompositeOperation = "copy";
      washLayer.drawImage(character.canvas, 0, 0);
      washLayer.globalCompositeOperation = "destination-in";
      washLayer.fillStyle = wash;
      washLayer.fillRect(0, 0, width, height);
      context.drawImage(washLayer.canvas, 0, 0);
    }

    shell.addEventListener("startup-frame", ({ detail }) => paint(detail.time, detail.idle));
    paint(timeline ? Number(timeline.currentTime ?? 0) : colorEnd);
  }).catch((error) => console.warn("Unable to load startup portrait", error));
})();
