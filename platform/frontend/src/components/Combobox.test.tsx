/**
 * The combobox: a selector that can be typed into, for lists long enough that
 * scrolling a native select is the slow way to find something.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Combobox, type ComboboxOption, Field } from "./primitives";

const COUNTRIES: ComboboxOption[] = [
  { value: "Caribbean/Cayman_Islands", label: "Cayman Islands (Caribbean)", detail: "CYM · KY", keywords: ["CYM", "KY"] },
  { value: "Europe/Iceland", label: "Iceland (Europe)", detail: "ISL · IS", keywords: ["ISL", "IS"] },
  { value: "Southeast_Asia/Indonesia", label: "Indonesia (Southeast Asia)", detail: "IDN · ID", keywords: ["IDN", "ID"] },
  {
    value: "Europe/Turkey",
    label: "Turkey (Europe)",
    detail: "No stock summary",
    disabled: true,
  },
  { value: "Europe/Turkiye", label: "Türkiye (Europe)", detail: "TUR · TR", keywords: ["TUR", "TR"] },
];

function Harness({ onChange = () => {} }: { onChange?: (value: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <Field label="Country" htmlFor="country">
      <Combobox
        id="country"
        placeholder="Choose a country"
        options={COUNTRIES}
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
      />
    </Field>
  );
}

describe("Combobox", () => {
  it("finds an option by typing part of its name", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.type(screen.getByLabelText("Country"), "indo");

    expect(screen.getAllByRole("option")).toHaveLength(1);
    await user.click(screen.getByRole("option", { name: /Indonesia/ }));
    expect(screen.getByLabelText("Country")).toHaveValue("Indonesia (Southeast Asia)");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("finds an option by a keyword it does not show, and ignores accents", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.type(screen.getByLabelText("Country"), "isl");
    // Both match; the exact code ranks Iceland ahead of the island listed before it.
    expect(screen.getAllByRole("option").map((item) => item.textContent)).toEqual([
      "Iceland (Europe)ISL · IS",
      "Cayman Islands (Caribbean)CYM · KY",
    ]);

    await user.clear(screen.getByLabelText("Country"));
    await user.type(screen.getByLabelText("Country"), "turkiye");
    expect(screen.getByRole("option", { name: /Türkiye/ })).toBeInTheDocument();
  });

  it("is driven from the keyboard, skipping options that cannot be chosen", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Harness onChange={onChange} />);

    const field = screen.getByLabelText("Country");
    await user.click(field);
    expect(field).toHaveAttribute("aria-expanded", "true");
    // Cayman Islands is active on opening; three steps pass Iceland, Indonesia
    // and the refused Turkey.
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{Enter}");

    expect(onChange).toHaveBeenCalledWith("Europe/Turkiye");
    expect(field).toHaveAttribute("aria-expanded", "false");
  });

  it("does not choose an option that cannot be built", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Harness onChange={onChange} />);

    await user.click(screen.getByLabelText("Country"));
    const refused = screen.getByRole("option", { name: /Turkey/ });
    expect(refused).toHaveAttribute("aria-disabled", "true");
    await user.click(refused);

    expect(onChange).not.toHaveBeenCalled();
  });

  it("says when nothing matches, and Escape puts the choice back", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const field = screen.getByLabelText("Country");
    await user.click(field);
    await user.click(screen.getByRole("option", { name: /Iceland/ }));
    await user.type(field, "atlantis");

    expect(screen.getByRole("status")).toHaveTextContent("Nothing matches");
    await user.keyboard("{Escape}");
    expect(field).toHaveValue("Iceland (Europe)");
  });

  it("does not call an empty list a failed search", async () => {
    const user = userEvent.setup();
    render(<Combobox aria-label="Portfolio" options={[]} value="" onChange={() => {}} />);

    await user.click(screen.getByLabelText("Portfolio"));

    expect(screen.getByRole("status")).toHaveTextContent("Nothing to choose from");
  });
});
