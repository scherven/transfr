import { useRef, useEffect, memo } from "react";

const TO_RADIANS = Math.PI / 180;
const WOBBLE = 10;
const MAX_TRAIL = 420;
const NUM_PARTICLES = 5;
const PARTICLE_RADIUS = 8;

const COLORS = ["#0a5fd0", "#ed0dba", "#6f2fe3", "#EE6C4D", "#38b000"];
const SPEEDS = [4.5, 3.2, 3.8, 2.8, 3.5];

function generateStart(count, width, height) {
  const positions = [];
  const angles = [];
  const buffer = 100;
  const ang = () => 20 + Math.floor(Math.random() * 50);

  for (let i = 0; i < count; i++) {
    const side = Math.floor(Math.random() * 4);
    const off = Math.floor(Math.random() * 100);
    switch (side) {
      case 0: {
        const x = buffer + Math.random() * (width - 2 * buffer);
        positions.push([x, -off]);
        angles.push(x < width / 2 ? ang() : ang() + 90);
        break;
      }
      case 1: {
        const y = buffer + Math.random() * (height - 2 * buffer);
        positions.push([width + off, y]);
        angles.push(y < height / 2 ? ang() + 90 : ang() + 180);
        break;
      }
      case 2: {
        const x = buffer + Math.random() * (width - 2 * buffer);
        positions.push([x, height + off]);
        angles.push(x < width / 2 ? ang() + 270 : ang() + 180);
        break;
      }
      case 3: {
        const y = buffer + Math.random() * (height - 2 * buffer);
        positions.push([-off, y]);
        angles.push(y < height / 2 ? ang() : ang() + 270);
        break;
      }
    }
  }
  return [positions, angles];
}

function dist(p1, p2) {
  return Math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2);
}

function cutLine(line, length) {
  const [p1, p2] = line;
  if (length < 0) return [p2, p2];

  const [x1, y1] = p1;
  const [x2, y2] = p2;
  const m = (y2 - y1) / (x2 - x1);

  let xmul = 1,
    ymul = 1;
  if (m < 0) {
    if (x2 < x1) xmul = -1;
    else if (y2 < y1) ymul = -1;
  } else if (m > 0) {
    if (x2 < x1) {
      xmul = -1;
      ymul = -1;
    }
  }

  const xp = x2 - xmul * (length / Math.sqrt(1 + m * m));
  const yp = y2 - ymul * (length / Math.sqrt(1 + 1 / (m * m)));
  return [[xp, yp], p2];
}

const Background = memo(function Background() {
  const canvasRef = useRef(null);
  const stateRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const [startPos, startAng] = generateStart(
      NUM_PARTICLES,
      canvas.width,
      canvas.height,
    );

    stateRef.current = {
      positions: startPos,
      angles: startAng,
      starting: Array(NUM_PARTICLES).fill(true),
      lines: Array.from({ length: NUM_PARTICLES }, () => []),
      currentLines: startPos.map((p) => [...p]),
    };

    let animId;

    function drawLine(p1, p2, color) {
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 3;
      ctx.moveTo(p1[0], p1[1]);
      ctx.lineTo(p2[0], p2[1]);
      ctx.stroke();
    }

    function render() {
      const s = stateRef.current;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      for (let idx = 0; idx < NUM_PARTICLES; idx++) {
        const color = COLORS[idx];

        // trim trail
        const dists = s.lines[idx].map((l) => dist(l[0], l[1]));
        let total = dists.reduce((a, b) => a + b, 0);
        const cl = [s.currentLines[idx], s.positions[idx]];
        const dcl = dist(cl[0], cl[1]);
        total += dcl;

        let i = 0;
        while (total > MAX_TRAIL) {
          if (s.lines[idx].length === 0) {
            const newLine = cutLine(cl, dcl - Math.max(0, total - MAX_TRAIL));
            s.currentLines[idx] = newLine[0];
            break;
          }
          if (dists[i] < total - MAX_TRAIL) {
            s.lines[idx].shift();
            total -= dists[i];
          } else {
            s.lines[idx][0] = cutLine(
              s.lines[idx][0],
              dists[i] - (total - MAX_TRAIL),
            );
            break;
          }
          i++;
        }

        // draw trail
        drawLine(s.currentLines[idx], s.positions[idx], color);
        for (const line of s.lines[idx]) {
          drawLine(line[0], line[1], color);
        }

        // draw particle
        const [x, y] = s.positions[idx];
        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.arc(x, y, PARTICLE_RADIUS, 0, 2 * Math.PI);
        ctx.fill();

        // move
        const angle = s.angles[idx];
        const nx = x + Math.cos(angle * TO_RADIANS) * SPEEDS[idx];
        const ny = y + Math.sin(angle * TO_RADIANS) * SPEEDS[idx];
        s.positions[idx] = [nx, ny];

        if (s.starting[idx]) {
          if (
            nx > PARTICLE_RADIUS &&
            nx < canvas.width - PARTICLE_RADIUS &&
            ny > PARTICLE_RADIUS &&
            ny < canvas.height - PARTICLE_RADIUS
          ) {
            s.starting[idx] = false;
          } else {
            continue;
          }
        }

        // bounce
        let bounce = false;
        if (nx <= PARTICLE_RADIUS || nx >= canvas.width - PARTICLE_RADIUS) {
          bounce = true;
          s.angles[idx] =
            s.angles[idx] > 180
              ? 540 - s.angles[idx]
              : 180 - s.angles[idx];
          s.positions[idx][0] =
            nx < PARTICLE_RADIUS
              ? PARTICLE_RADIUS
              : canvas.width - PARTICLE_RADIUS;
        } else if (
          ny <= PARTICLE_RADIUS ||
          ny >= canvas.height - PARTICLE_RADIUS
        ) {
          bounce = true;
          s.angles[idx] = 360 - s.angles[idx];
          s.positions[idx][1] =
            ny < PARTICLE_RADIUS
              ? PARTICLE_RADIUS
              : canvas.height - PARTICLE_RADIUS;
        }

        if (bounce) {
          s.lines[idx].push([s.currentLines[idx], [nx, ny]]);
          s.currentLines[idx] = [nx, ny];
          s.angles[idx] += Math.floor(Math.random() * WOBBLE) - WOBBLE / 2;
          s.angles[idx] %= 360;
        }
      }

      animId = requestAnimationFrame(render);
    }

    const timeout = setTimeout(render, 400);

    function handleResize() {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    }
    window.addEventListener("resize", handleResize);

    return () => {
      clearTimeout(timeout);
      cancelAnimationFrame(animId);
      window.removeEventListener("resize", handleResize);
    };
  }, []);

  return <canvas ref={canvasRef} id="background-canvas" />;
});

export default Background;
