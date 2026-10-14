const assert = require("node:assert/strict");
const { dayNumber, parseCampaign, addDays, buildQueue } = require("../lib.js");

assert.equal(dayNumber("day-10.png"), 10);
assert.equal(dayNumber("3SVerse-Reel-02.mp4"), 2);

const markdown = `# Campaign
## Day 1 — First
Full first caption.

#VidaPay #3SVerse

![Creative](images/day-01.png)
---
## Day 2 — Second
Full second caption.

#LinkedIn #WirelessRetail
`;
const captions = parseCampaign(markdown);
assert.deepEqual(captions.map(item => item.day), [1, 2]);
assert.equal(captions[0].caption, "Full first caption.\n\n#VidaPay #3SVerse");
assert.match(captions[1].caption, /#LinkedIn #WirelessRetail$/);

const images = [{ name: "day-01.png" }, { name: "day-02.jpg" }];
const videos = [{ name: "3SVerse-Reel-01.mp4" }, { name: "3SVerse-Reel-02.mp4" }];
assert.equal(addDays("2026-10-13", 2), "2026-10-15");
const imageQueue = buildQueue(captions, images, videos, { image: true, video: false }, 1, "2026-10-13", "09:00");
assert.deepEqual(imageQueue.map(item => `${item.day}:${item.type}:${item.date}:${item.time}`), ["1:image:2026-10-13:09:00", "2:image:2026-10-14:09:00"]);
const videoQueue = buildQueue(captions, images, videos, { image: true, video: true }, 2, "2026-10-20", "10:30");
assert.deepEqual(videoQueue.map(item => `${item.day}:${item.type}:${item.date}:${item.time}`), ["2:video:2026-10-20:10:30"]);
console.log("LinkedIn campaign parser and queue tests passed.");
