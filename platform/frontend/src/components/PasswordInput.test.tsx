/**
 * The password field: hidden by default, readable by whoever is at the
 * keyboard, because a password typed blind is a password mistyped.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Field, PasswordInput } from "./primitives";

function Harness() {
  const [value, setValue] = useState("");
  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <Field label="Password" htmlFor="password" required>
        <PasswordInput
          id="password"
          name="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </Field>
    </form>
  );
}

describe("PasswordInput", () => {
  it("hides what is typed until somebody asks to see it", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const field = screen.getByLabelText(/^password/i);
    await user.type(field, "cass-demo-password");

    expect(field).toHaveAttribute("type", "password");

    await user.click(screen.getByRole("button", { name: "Show password" }));

    expect(field).toHaveAttribute("type", "text");
    expect(field).toHaveValue("cass-demo-password");
  });

  it("hides it again, and says which way round it is", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const field = screen.getByLabelText(/^password/i);

    await user.click(screen.getByRole("button", { name: "Show password" }));
    await user.click(screen.getByRole("button", { name: "Hide password" }));

    expect(field).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Show password" })).toBeInTheDocument();
  });

  it("does not submit the form when it is pressed", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    // A button inside a form submits it unless it says otherwise, and reading
    // a password back must never be an attempt to sign in.
    expect(screen.getByRole("button", { name: "Show password" })).toHaveAttribute(
      "type",
      "button",
    );
    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(screen.getByLabelText(/^password/i)).toHaveAttribute("type", "text");
  });
});
