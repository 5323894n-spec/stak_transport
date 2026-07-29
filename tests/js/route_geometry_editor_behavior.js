"use strict";

const assert = require("node:assert/strict");
const editor = require("../../static/route-geometry-editor.js");

const anchors = [[35.9, 56.8], [35.92, 56.82]];
const geometry = {
  type: "LineString",
  coordinates: [anchors[0], [35.91, 56.81], anchors[1]],
};

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function draftLifecycleAndCancel() {
  const draft = editor.createDraft(geometry, anchors, 4);
  assert.equal(draft.version, 4);
  assert.equal(editor.isDirty(draft), false);
  assert.deepEqual([...draft.anchorIndexes], [0, 2]);
  assert.deepEqual([...draft.userIndexes], []);
  assert.equal(draft.selectedIndex, null);

  editor.moveVertex(draft, 1, [35.911, 56.811]);
  editor.insertVertex(draft, 1, [35.915, 56.815]);
  assert.equal(editor.isDirty(draft), true);
  editor.cancelDraft(draft);

  assert.equal(editor.isDirty(draft), false);
  assert.deepEqual(draft.coordinates, geometry.coordinates);
  assert.deepEqual([...draft.anchorIndexes], [0, 2]);
  assert.deepEqual([...draft.userIndexes], []);
  assert.equal(draft.selectedIndex, null);
}

function anchorsAndEndpointsAreLocked() {
  const withInteriorAnchors = {
    type: "LineString",
    coordinates: [[35.89, 56.79], ...geometry.coordinates, [35.93, 56.83]],
  };
  const draft = editor.createDraft(withInteriorAnchors, anchors, 1);
  assert.deepEqual([...draft.anchorIndexes], [1, 3]);
  assert.throws(() => editor.moveVertex(draft, 1, [1, 1]), /остановки/);
  assert.throws(() => editor.deleteVertex(draft, 3), /остановки/);
  assert.throws(() => editor.moveVertex(draft, 0, [1, 1]), /конечную/);
  assert.throws(() => editor.deleteVertex(draft, 4), /конечную/);
}

function insertAndDeleteShiftAllIndexes() {
  const draft = editor.createDraft(geometry, anchors, 1);
  draft.selectedIndex = 2;
  const inserted = editor.insertVertex(draft, 0, [35.905, 56.805]);
  assert.equal(inserted, 1);
  assert.deepEqual(draft.coordinates[1], [35.905, 56.805]);
  assert.deepEqual([...draft.anchorIndexes], [0, 3]);
  assert.deepEqual([...draft.userIndexes], [1]);
  assert.equal(draft.selectedIndex, 1);

  const second = editor.insertVertex(draft, 1, [35.907, 56.807]);
  assert.equal(second, 2);
  draft.selectedIndex = 4;
  editor.deleteVertex(draft, 1);
  assert.deepEqual([...draft.anchorIndexes], [0, 3]);
  assert.deepEqual([...draft.userIndexes], [1]);
  assert.equal(draft.selectedIndex, 3);
  assert.deepEqual(draft.coordinates[1], [35.907, 56.807]);
  assert.equal(draft.coordinates.length, 4);
}

function sparseMarkersAreDeterministicAndKeepRequiredPoints() {
  const coordinates = Array.from(
    { length: 500 },
    (_, index) => [35.9 + index / 10000, 56.8],
  );
  const draft = editor.createDraft(
    { type: "LineString", coordinates },
    [coordinates[0], coordinates[499]],
    1,
  );
  draft.userIndexes.add(123);
  const first = editor.visibleVertexIndexes(draft, 120);
  const second = editor.visibleVertexIndexes(draft, 120);
  assert.deepEqual(first, second);
  assert.equal(first.length, 120);
  assert.ok(first.includes(0) && first.includes(123) && first.includes(499));
  assert.deepEqual(first, [...first].sort((left, right) => left - right));

  const manyRequired = editor.createDraft(
    { type: "LineString", coordinates: coordinates.slice(0, 130) },
    [coordinates[0], coordinates[129]],
    1,
  );
  for (let index = 1; index < 129; index += 1) manyRequired.userIndexes.add(index);
  assert.equal(editor.visibleVertexIndexes(manyRequired, 120).length, 130);
}

function nearestSegmentHandlesTiesAndZeroLengthSegments() {
  assert.equal(
    editor.nearestSegmentIndex([7, 1], [[0, 0], [5, 0], [10, 0]]),
    1,
  );
  assert.equal(
    editor.nearestSegmentIndex([5, 1], [[0, 0], [5, 0], [10, 0]]),
    0,
    "an exact tie must select the earliest segment",
  );
  assert.equal(
    editor.nearestSegmentIndex([0, 1], [[0, 0], [0, 0], [10, 0]]),
    0,
    "a zero-length segment must be measured from its endpoint",
  );
}

function inputsAndPayloadsAreDeeplyCloned() {
  const inputGeometry = plain(geometry);
  const inputAnchors = plain(anchors);
  const draft = editor.createDraft(inputGeometry, inputAnchors, 2);
  inputGeometry.coordinates[1][0] = 99;
  inputAnchors[0][0] = 99;
  assert.deepEqual(draft.coordinates, geometry.coordinates);
  assert.deepEqual(draft.original, geometry.coordinates);

  editor.moveVertex(draft, 1, [35.911, 56.811]);
  assert.deepEqual(draft.original, geometry.coordinates);
  const payload = editor.geometryPayload(draft);
  assert.deepEqual(payload, { type: "LineString", coordinates: draft.coordinates });
  payload.coordinates[1][0] = 100;
  assert.equal(draft.coordinates[1][0], 35.911);
}

function invalidInputsAndIndexesAreRejected() {
  const invalidDraftInputs = [
    [null, anchors, 1],
    [{ type: "Point", coordinates: geometry.coordinates }, anchors, 1],
    [{ type: "LineString", coordinates: [[35.9, 56.8]] }, anchors, 1],
    [{ type: "LineString", coordinates: [[35.9, 56.8], [NaN, 56.9]] }, [], 1],
    [geometry, "not anchors", 1],
    [geometry, [[35.9]], 1],
    [geometry, anchors, -1],
  ];
  for (const args of invalidDraftInputs) {
    assert.throws(() => editor.createDraft(...args), Error);
  }

  assert.throws(
    () => editor.createDraft(geometry, [anchors[1], anchors[0]], 1),
    /порядке/,
  );
  assert.throws(
    () => editor.createDraft(geometry, [[35.905, 56.805]], 1),
    /соответствует/,
  );
  assert.throws(
    () => editor.createDraft(
      {
        type: "LineString",
        coordinates: [anchors[0], anchors[0], anchors[1]],
      },
      anchors,
      1,
    ),
    /однозначно/,
  );

  const draft = editor.createDraft(geometry, anchors, 1);
  for (const index of [-1, 1.5, 3, "1"]) {
    assert.throws(() => editor.moveVertex(draft, index, [35.91, 56.81]), /индекс/i);
    assert.throws(() => editor.deleteVertex(draft, index), /индекс/i);
  }
  for (const segmentIndex of [-1, 1.5, 2, "0"]) {
    assert.throws(
      () => editor.insertVertex(draft, segmentIndex, [35.91, 56.81]),
      /индекс/i,
    );
  }
  assert.throws(() => editor.moveVertex(draft, 1, [200, 56.8]), /координат/i);
  assert.throws(() => editor.visibleVertexIndexes(draft, 0), /лимит/i);
  assert.throws(() => editor.nearestSegmentIndex([0, 0], [[0, 0]]), /минимум/);
  assert.throws(
    () => editor.nearestSegmentIndex([NaN, 0], [[0, 0], [1, 1]]),
    /точка/i,
  );
  assert.throws(
    () => editor.nearestSegmentIndex([0, 0], [[0, 0], [Infinity, 1]]),
    /точка/i,
  );
}

function sourceLabelsAreLocalized() {
  assert.equal(editor.sourceLabel("manual"), "Ручная геометрия");
  assert.equal(editor.sourceLabel("osrm"), "Геометрия OSRM");
  assert.equal(editor.sourceLabel("stops"), "Линия по остановкам");
  assert.equal(editor.sourceLabel(null), "Линия по остановкам");
}

const scenarios = {
  draft_lifecycle: draftLifecycleAndCancel,
  anchors_locked: anchorsAndEndpointsAreLocked,
  insert_delete: insertAndDeleteShiftAllIndexes,
  sparse: sparseMarkersAreDeterministicAndKeepRequiredPoints,
  nearest: nearestSegmentHandlesTiesAndZeroLengthSegments,
  cloning: inputsAndPayloadsAreDeeplyCloned,
  invalid: invalidInputsAndIndexesAreRejected,
  labels: sourceLabelsAreLocalized,
};

const scenario = process.argv[2];
if (scenario) {
  assert.ok(scenarios[scenario], `unknown scenario: ${scenario}`);
  scenarios[scenario]();
} else {
  Object.values(scenarios).forEach(run => run());
}
