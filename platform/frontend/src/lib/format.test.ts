/**
 * Formatting, with attention to the precision rule.
 *
 * Monetary values cross the API as strings because they are Decimal on the
 * server. These tests pin the behaviour at the boundary so a refactor cannot
 * quietly start rounding a portfolio total.
 */

import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatCount,
  formatDuration,
  formatMoney,
  formatMoneyExact,
  formatPercent,
} from "./format";

describe("formatMoney", () => {
  it("shows an em dash for an absent value rather than zero", () => {
    // Zero loss and unknown loss are different answers.
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
    expect(formatMoney("")).toBe("—");
    expect(formatMoney("0")).toBe("0");
  });

  it("compacts large values for dense tables", () => {
    expect(formatMoney("9900000")).toBe("9.9M");
    expect(formatMoney("1250000000")).toBe("1.3B");
  });

  it("shows values below a million in full", () => {
    expect(formatMoney("450000")).toBe("450,000");
  });

  it("returns the original text when a value is not numeric", () => {
    expect(formatMoney("not a number")).toBe("not a number");
  });
});

describe("formatMoneyExact", () => {
  it("keeps two decimal places for audit and export", () => {
    expect(formatMoneyExact("1000000.01")).toBe("1,000,000.01");
  });
});

describe("formatCount", () => {
  it("groups thousands", () => {
    expect(formatCount(12345)).toBe("12,345");
  });

  it("distinguishes zero from unknown", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(null)).toBe("—");
  });
});

describe("formatPercent", () => {
  it("renders a proportion as a percentage", () => {
    expect(formatPercent(0.425)).toBe("42.5%");
    expect(formatPercent(0.425, 0)).toBe("43%");
  });
});

describe("formatDuration", () => {
  it("scales the unit to the elapsed time", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(125)).toBe("2m 5s");
    expect(formatDuration(7325)).toBe("2h 2m");
  });

  it("distinguishes a zero-second run from an unrecorded one", () => {
    expect(formatDuration(0)).toBe("0s");
    expect(formatDuration(null)).toBe("—");
  });
});

describe("formatBytes", () => {
  it("scales through the units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(21.6 * 1024 ** 3)).toBe("21.6 GB");
  });
});
