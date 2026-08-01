import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it } from "vitest";

import { DailyUnavailabilityIndicator } from "@/components/DailyUnavailabilityIndicator";

describe("DailyUnavailabilityIndicator", () => {
  it("stays compact and opens exact unavailable times accessibly", async () => {
    const user = userEvent.setup();
    render(
      <DailyUnavailabilityIndicator
        selectedDate="2026-08-01"
        people={[
          { external_person_id: 7, first_name: "Jane", last_name: "Doe" },
        ]}
        intervals={[
          {
            person_id: 7,
            working_date: "2026-08-01",
            start: "2026-08-02T00:30:00",
            end: "2026-08-02T02:00:00",
          },
        ]}
      />,
    );

    const trigger = screen.getByRole("button", {
      name: "1 person unavailable on the selected day",
    });
    expect(trigger).toHaveTextContent("1");
    expect(screen.queryByText("Jane Doe")).not.toBeInTheDocument();

    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Unavailable on this working day" })).toBeInTheDocument();
    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
    expect(screen.getByText("00:30 - 02:00")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("uses a deterministic 24-hour clock on the working date", async () => {
    const user = userEvent.setup();
    render(
      <DailyUnavailabilityIndicator
        selectedDate="2026-08-01"
        people={[
          { external_person_id: 7, first_name: "Jane", last_name: "Doe" },
        ]}
        intervals={[
          {
            person_id: 7,
            working_date: "2026-08-01",
            start: "2026-08-01T09:05:00",
            end: "2026-08-01T10:30:00",
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", {
      name: "1 person unavailable on the selected day",
    }));

    expect(screen.getByText("09:05 - 10:30")).toBeInTheDocument();
  });

  it("labels unavailability covering midnight to midnight as whole day", async () => {
    const user = userEvent.setup();
    render(
      <DailyUnavailabilityIndicator
        selectedDate="2026-08-01"
        people={[
          { external_person_id: 7, first_name: "Jane", last_name: "Doe" },
        ]}
        intervals={[
          {
            person_id: 7,
            working_date: "2026-08-01",
            start: "2026-08-01T00:00:00",
            end: "2026-08-02T00:00:00",
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", {
      name: "1 person unavailable on the selected day",
    }));

    expect(screen.getByText("Whole day")).toBeInTheDocument();
    expect(screen.queryByText("00:00 - 00:00")).not.toBeInTheDocument();
  });

  it("renders nothing when nobody is unavailable on the selected day", () => {
    const { container } = render(
      <DailyUnavailabilityIndicator
        selectedDate="2026-08-01"
        people={[]}
        intervals={[]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
