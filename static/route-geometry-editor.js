(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RouteGeometryEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ANCHOR_TOLERANCE = 0.000001;

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function validateGeoPoint(point) {
    if (
      !Array.isArray(point)
      || point.length !== 2
      || !isFiniteNumber(point[0])
      || !isFiniteNumber(point[1])
      || point[0] < -180
      || point[0] > 180
      || point[1] < -90
      || point[1] > 90
    ) {
      throw new Error("Некорректная координата [долгота, широта]");
    }
  }

  function validateScreenPoint(point) {
    if (
      !Array.isArray(point)
      || point.length !== 2
      || !isFiniteNumber(point[0])
      || !isFiniteNumber(point[1])
    ) {
      throw new Error("Некорректная экранная точка");
    }
  }

  function cloneCoordinates(coordinates) {
    return coordinates.map(point => [point[0], point[1]]);
  }

  function sameCoordinate(left, right) {
    return (
      Math.abs(left[0] - right[0]) <= ANCHOR_TOLERANCE
      && Math.abs(left[1] - right[1]) <= ANCHOR_TOLERANCE
    );
  }

  function validateDraft(draft) {
    if (
      !draft
      || !Array.isArray(draft.original)
      || !Array.isArray(draft.coordinates)
      || !(draft.anchorIndexes instanceof Set)
      || !(draft.userIndexes instanceof Set)
    ) {
      throw new Error("Некорректный черновик геометрии");
    }
  }

  function validateVertexIndex(draft, index) {
    if (
      !Number.isInteger(index)
      || index < 0
      || index >= draft.coordinates.length
    ) {
      throw new Error("Некорректный индекс вершины");
    }
  }

  function validateSegmentIndex(draft, index) {
    if (
      !Number.isInteger(index)
      || index < 0
      || index >= draft.coordinates.length - 1
    ) {
      throw new Error("Некорректный индекс сегмента");
    }
  }

  function mapAnchorIndexes(coordinates, anchors) {
    const indexes = [];
    let previous = -1;
    for (const anchor of anchors) {
      const matches = [];
      for (let index = 0; index < coordinates.length; index += 1) {
        if (sameCoordinate(coordinates[index], anchor)) matches.push(index);
      }
      if (matches.length === 0) {
        throw new Error("Якорь остановки не соответствует линии");
      }
      if (matches.length !== 1 || indexes.includes(matches[0])) {
        throw new Error("Якоря остановок должны сопоставляться однозначно");
      }
      if (matches[0] <= previous) {
        throw new Error("Якоря остановок должны идти в порядке маршрута");
      }
      indexes.push(matches[0]);
      previous = matches[0];
    }
    return new Set(indexes);
  }

  function createDraft(geometry, anchors, version) {
    if (
      !geometry
      || geometry.type !== "LineString"
      || !Array.isArray(geometry.coordinates)
      || geometry.coordinates.length < 2
    ) {
      throw new Error("Геометрия должна быть LineString минимум из двух точек");
    }
    if (!Array.isArray(anchors)) {
      throw new Error("Якоря остановок должны быть массивом");
    }
    if (!Number.isInteger(version) || version < 0) {
      throw new Error("Версия геометрии должна быть неотрицательным целым числом");
    }
    geometry.coordinates.forEach(validateGeoPoint);
    anchors.forEach(validateGeoPoint);

    const coordinates = cloneCoordinates(geometry.coordinates);
    const anchorIndexes = mapAnchorIndexes(coordinates, anchors);
    return {
      original: cloneCoordinates(coordinates),
      coordinates,
      anchorIndexes,
      userIndexes: new Set(),
      selectedIndex: null,
      version,
    };
  }

  function isDirty(draft) {
    validateDraft(draft);
    return JSON.stringify(draft.coordinates) !== JSON.stringify(draft.original);
  }

  function cancelDraft(draft) {
    validateDraft(draft);
    const anchorPoints = [...draft.anchorIndexes]
      .sort((left, right) => left - right)
      .map(index => draft.coordinates[index]);
    draft.coordinates = cloneCoordinates(draft.original);
    draft.anchorIndexes = mapAnchorIndexes(draft.coordinates, anchorPoints);
    draft.userIndexes = new Set();
    draft.selectedIndex = null;
  }

  function assertEditableVertex(draft, index) {
    if (draft.anchorIndexes.has(index)) {
      throw new Error("Нельзя изменять вершину остановки");
    }
    if (index === 0 || index === draft.coordinates.length - 1) {
      throw new Error("Нельзя изменять обязательную конечную вершину");
    }
  }

  function moveVertex(draft, index, point) {
    validateDraft(draft);
    validateVertexIndex(draft, index);
    validateGeoPoint(point);
    assertEditableVertex(draft, index);
    draft.coordinates[index] = [point[0], point[1]];
    return index;
  }

  function shiftSetForInsert(indexes, insertedIndex) {
    return new Set(
      [...indexes].map(index => index >= insertedIndex ? index + 1 : index),
    );
  }

  function insertVertex(draft, segmentIndex, point) {
    validateDraft(draft);
    validateSegmentIndex(draft, segmentIndex);
    validateGeoPoint(point);
    const index = segmentIndex + 1;
    draft.coordinates.splice(index, 0, [point[0], point[1]]);
    draft.anchorIndexes = shiftSetForInsert(draft.anchorIndexes, index);
    draft.userIndexes = shiftSetForInsert(draft.userIndexes, index);
    draft.userIndexes.add(index);
    draft.selectedIndex = index;
    return index;
  }

  function shiftSetForDelete(indexes, deletedIndex) {
    return new Set(
      [...indexes]
        .filter(index => index !== deletedIndex)
        .map(index => index > deletedIndex ? index - 1 : index),
    );
  }

  function deleteVertex(draft, index) {
    validateDraft(draft);
    validateVertexIndex(draft, index);
    assertEditableVertex(draft, index);
    if (draft.coordinates.length <= 2) {
      throw new Error("LineString должна содержать минимум две точки");
    }
    draft.coordinates.splice(index, 1);
    draft.anchorIndexes = shiftSetForDelete(draft.anchorIndexes, index);
    draft.userIndexes = shiftSetForDelete(draft.userIndexes, index);
    if (draft.selectedIndex === index) draft.selectedIndex = null;
    else if (draft.selectedIndex > index) draft.selectedIndex -= 1;
    return index;
  }

  function visibleVertexIndexes(draft, limit) {
    validateDraft(draft);
    const effectiveLimit = limit === undefined ? 120 : limit;
    if (!Number.isInteger(effectiveLimit) || effectiveLimit < 1) {
      throw new Error("Лимит видимых вершин должен быть положительным целым числом");
    }

    const lastIndex = draft.coordinates.length - 1;
    const required = new Set([
      0,
      lastIndex,
      ...draft.anchorIndexes,
      ...draft.userIndexes,
    ]);
    const result = [...required].sort((left, right) => left - right);
    const slots = effectiveLimit - result.length;
    if (slots <= 0) return result;

    const candidates = [];
    for (let index = 0; index <= lastIndex; index += 1) {
      if (!required.has(index)) candidates.push(index);
    }
    if (candidates.length <= slots) {
      return [...result, ...candidates].sort((left, right) => left - right);
    }

    for (let slot = 0; slot < slots; slot += 1) {
      const candidateIndex = Math.floor((slot + 0.5) * candidates.length / slots);
      required.add(candidates[candidateIndex]);
    }
    return [...required].sort((left, right) => left - right);
  }

  function pointToSegmentDistanceSquared(point, start, end) {
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const lengthSquared = dx * dx + dy * dy;
    const ratio = lengthSquared === 0
      ? 0
      : Math.max(
        0,
        Math.min(
          1,
          ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / lengthSquared,
        ),
      );
    const projectedX = start[0] + ratio * dx;
    const projectedY = start[1] + ratio * dy;
    return (
      (point[0] - projectedX) ** 2
      + (point[1] - projectedY) ** 2
    );
  }

  function nearestSegmentIndex(point, projected) {
    validateScreenPoint(point);
    if (!Array.isArray(projected) || projected.length < 2) {
      throw new Error("Линия должна содержать минимум две экранные точки");
    }
    projected.forEach(validateScreenPoint);

    let nearest = 0;
    let best = pointToSegmentDistanceSquared(
      point,
      projected[0],
      projected[1],
    );
    for (let index = 1; index < projected.length - 1; index += 1) {
      const distance = pointToSegmentDistanceSquared(
        point,
        projected[index],
        projected[index + 1],
      );
      if (distance < best) {
        nearest = index;
        best = distance;
      }
    }
    return nearest;
  }

  function geometryPayload(draft) {
    validateDraft(draft);
    return {
      type: "LineString",
      coordinates: cloneCoordinates(draft.coordinates),
    };
  }

  function sourceLabel(source) {
    if (source === "manual") return "Ручная геометрия";
    if (source === "osrm") return "Геометрия OSRM";
    return "Линия по остановкам";
  }

  return {
    createDraft,
    isDirty,
    cancelDraft,
    moveVertex,
    insertVertex,
    deleteVertex,
    visibleVertexIndexes,
    nearestSegmentIndex,
    geometryPayload,
    sourceLabel,
  };
});
