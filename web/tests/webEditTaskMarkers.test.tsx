import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";

import { CalendarGrid } from "@/components/CalendarGrid";
import { TaskDetailModal, type Task } from "@/components/TaskDetailModal";

const editedTask: Task = {
  id: 1,
  external_task_id: 101,
  name: "Opening Briefing",
  summary: null,
  description: null,
  start: "2026-05-21T09:00:00",
  end: "2026-05-21T10:00:00",
  location_name: "Room A",
  location_address: null,
  task_type_code: "session",
  task_type_name: "Session",
  color: "#4A90D9",
  attendees: [],
  field_assignments: null,
  field_values: null,
  field_definitions: null,
  additional: null,
  sort_order: 0,
  has_web_edit: true,
  web_edit_edited_at: "2026-05-21T14:20:00",
  web_edit_edited_by: "Anna",
  web_edit_edited_by_user_id: 7,
  web_edit_change_summary: ["Time changed"],
};

describe("web edit task markers", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses the desktop-style pencil indicator on calendar tasks", () => {
    render(
      <CalendarGrid
        tasks={[editedTask]}
        selectedDate="2026-05-21"
        highlightedPersonId={null}
        highlightMode="off"
        onTaskDoubleClick={vi.fn()}
      />,
    );

    const marker = screen.getByLabelText(
      /Edited on the web by Anna .* at 14:20\. Time changed\./,
    );
    expect(marker.className).toContain("rounded-full");
    expect(marker.className).toContain("bg-amber-500/10");
    expect(marker.querySelector("svg")).not.toBeNull();
  });

  it("uses the same pencil indicator in the task detail modal", () => {
    render(
      <TaskDetailModal
        task={editedTask}
        canEdit
        eventId={1}
        dataPolicyAcknowledged
        persons={[]}
        onClose={vi.fn()}
        onDataChanged={vi.fn()}
        onDraftEdit={vi.fn()}
        onDraftDelete={vi.fn()}
      />,
    );

    const markers = screen.getAllByLabelText(
      /Edited on the web by Anna .* at 14:20\. Time changed\./,
    );
    expect(markers[0].className).toContain("rounded-full");
    expect(markers[0].querySelector("svg")).not.toBeNull();
    expect(screen.getByText("Edited on the web")).toBeInTheDocument();
  });

  it("keeps the current-time indicator below modals and centers its marker", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-21T09:30:00"));

    const { container } = render(
      <CalendarGrid
        tasks={[{ ...editedTask, has_web_edit: false }]}
        selectedDate="2026-05-21"
        highlightedPersonId={null}
        highlightMode="off"
        onTaskDoubleClick={vi.fn()}
      />,
    );

    const indicator = container.querySelector(
      'div[class*="z-30"][class*="pointer-events-none"]',
    );
    expect(indicator).not.toBeNull();
    expect(indicator?.className).not.toContain("z-[100]");

    const centeredLayer = indicator?.firstElementChild as HTMLElement | null;
    expect(centeredLayer?.className).toContain("h-[10px]");
    expect(centeredLayer?.className).toContain("-translate-y-1/2");

    const marker = centeredLayer?.children[0] as HTMLElement | undefined;
    const line = centeredLayer?.children[1] as HTMLElement | undefined;
    expect(marker?.className).toContain("top-1/2");
    expect(marker?.className).toContain("-translate-y-1/2");
    expect(line?.className).toContain("top-1/2");
    expect(line?.className).toContain("-translate-y-1/2");
  });

  it("drafts structured assignment edits without flattening categories", () => {
    const onDraftEdit = vi.fn();
    const structuredTask: Task = {
      ...editedTask,
      has_web_edit: false,
      attendees: [
        { name: "Person A", person_id: 1 },
        { name: "Person B", person_id: 2 },
      ],
      field_assignments: {
        driver: [{ name: "Person A", person_id: 1 }],
        cook: [{ name: "Person B", person_id: 2 }],
      },
      field_definitions: [
        { id: "driver", name: "Driver", type: "persons_list", purpose: "assignment", visibility: "participant" },
        { id: "cook", name: "Cook", type: "persons_list", purpose: "assignment", visibility: "participant" },
      ],
    };

    const { container } = render(
      <TaskDetailModal
        task={structuredTask}
        canEdit
        eventId={1}
        dataPolicyAcknowledged
        persons={[
          { id: 1, external_person_id: 1, first_name: "Person", last_name: "A" },
          { id: 2, external_person_id: 2, first_name: "Person", last_name: "B" },
          { id: 3, external_person_id: 3, first_name: "Person", last_name: "C" },
        ]}
        onClose={vi.fn()}
        onDataChanged={vi.fn()}
        onDraftEdit={onDraftEdit}
        onDraftDelete={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTitle("Edit task"));
    expect(screen.getByText("Driver")).toBeInTheDocument();
    expect(screen.getByText("Cook")).toBeInTheDocument();

    const selects = container.querySelectorAll("select");
    fireEvent.change(selects[1], { target: { value: "3" } });
    fireEvent.click(screen.getByText("Save Draft"));

    expect(onDraftEdit).toHaveBeenCalledWith(
      structuredTask.id,
      expect.objectContaining({
        field_assignments: {
          driver: [{ name: "Person A", person_id: 1 }],
          cook: [
            { name: "Person B", person_id: 2 },
            { name: "Person C", person_id: 3 },
          ],
        },
        attendees: [
          { name: "Person A", person_id: 1 },
          { name: "Person B", person_id: 2 },
          { name: "Person C", person_id: 3 },
        ],
      }),
    );
  });
});
