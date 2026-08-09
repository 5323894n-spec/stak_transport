"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const context = vm.createContext({ console, VIEWS: {} });
context.window = context;
vm.runInContext(
  fs.readFileSync(path.resolve(__dirname, "../../static/dispatch.js"), "utf8"),
  context,
);

const label = vm.runInContext("dispatchDeviationLabel", context);
assert.equal(label(4), "+4′");
assert.equal(label(-3), "−3′");
assert.equal(label(0), "0′");
assert.equal(label(null), "—");

const onTime = vm.runInContext("dispatchOnTime", context);
assert.equal(onTime(1, 2), true);
assert.equal(onTime(3, 2), false);
assert.equal(onTime(-2, 2), true);

console.log("dispatch deviation OK");
