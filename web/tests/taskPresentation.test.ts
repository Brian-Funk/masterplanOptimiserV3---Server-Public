import { describe, expect, it } from "vitest";
import {
  taskAllocations,
  taskOperationalFields,
  taskTypeBadge,
} from "@/lib/taskPresentation";

const structuredTask = {
  name: "Registration desk",
  task_type_name: "Desk duty",
  attendees: [
    { name: "Brian Funk", person_id: 1 },
    { name: "Anna Example", person_id: 2 },
  ],
  field_assignments: {
    front: [{ name: "Brian Funk", person_id: 1 }],
    side: [{ name: "Anna Example", person_id: 2 }],
  },
  field_values: {
    first_note: "First paragraph\nSecond paragraph",
    second_note: "Another value",
    handbook: { url: "https://example.test/handbook", text: "Handbook" },
    room: { name: "Assembly Hall", address: "Synthetic Street 1" },
    required: ["Chairing", "Minutes"],
    duration: 45,
    window: { start: "09:00", end: "10:00" },
  },
  field_definitions: [
    { id: "front", name: "Front-Orga", type: "persons_list" },
    { id: "side", name: "Side-Orga", type: "persons_list" },
    { id: "first_note", name: "Field_A", type: "text" },
    { id: "second_note", name: "Field_B", type: "text" },
    { id: "handbook", name: "Reference", type: "link" },
    { id: "room", name: "Meeting place", type: "location" },
    { id: "required", name: "Capabilities", type: "capabilities_list" },
    { id: "duration", name: "Duration", type: "duration" },
    { id: "window", name: "Window", type: "start_end_time" },
  ],
};

describe("task presentation", () => {
  it("preserves allocation category and attendee order without deduplication", () => {
    expect(taskAllocations(structuredTask)).toEqual([
      {
        fieldId: "front",
        label: "Front-Orga",
        attendees: [{ name: "Brian Funk", person_id: 1 }],
        legacy: false,
      },
      {
        fieldId: "side",
        label: "Side-Orga",
        attendees: [{ name: "Anna Example", person_id: 2 }],
        legacy: false,
      },
    ]);
  });

  it("uses the flat attendee list only for legacy tasks", () => {
    expect(taskAllocations({
      name: "Legacy",
      attendees: [{ name: "Legacy Person", person_id: 9 }],
    })).toEqual([{
      fieldId: null,
      label: null,
      attendees: [{ name: "Legacy Person", person_id: 9 }],
      legacy: true,
    }]);
  });

  it("renders every bounded operational field as one labelled row", () => {
    expect(taskOperationalFields(structuredTask)).toEqual([
      { fieldId: "first_note", label: "Field_A", type: "text", value: "First paragraph\nSecond paragraph" },
      { fieldId: "second_note", label: "Field_B", type: "text", value: "Another value" },
      { fieldId: "handbook", label: "Reference", type: "link", value: "Handbook", href: "https://example.test/handbook" },
      { fieldId: "room", label: "Meeting place", type: "location", value: "Assembly Hall", secondary: "Synthetic Street 1" },
      { fieldId: "required", label: "Capabilities", type: "capabilities_list", value: "Chairing, Minutes" },
      { fieldId: "duration", label: "Duration", type: "duration", value: "45" },
      { fieldId: "window", label: "Window", type: "start_end_time", value: "09:00 – 10:00" },
    ]);
  });

  it("omits a redundant task-type badge", () => {
    expect(taskTypeBadge(structuredTask)).toBe("Desk duty");
    expect(taskTypeBadge({ ...structuredTask, task_type_name: "Registration desk" })).toBeNull();
  });
});
