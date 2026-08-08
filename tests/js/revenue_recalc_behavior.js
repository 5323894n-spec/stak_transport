"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const context = vm.createContext({ console, VIEWS: {} });
context.window = context;
vm.runInContext(
  fs.readFileSync(path.resolve(__dirname, "../../static/revenue.js"), "utf8"),
  context,
);

const lines = [
  { unit_price: 30, tickets_count: 100 },
  { unit_price: 15, tickets_count: 20 },
];
const total = vm.runInContext("revenueRecalcExpected", context)(lines);
assert.equal(total, 3300);

const emptyTotal = vm.runInContext("revenueRecalcExpected", context)([]);
assert.equal(emptyTotal, 0);

console.log("revenue recalc OK");
