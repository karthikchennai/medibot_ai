import type { Role } from "./api";

export const ROLE_LABELS: Record<Role, string> = {
  doctor: "Doctor", nurse: "Nurse", billing_executive: "Billing executive", billing: "Billing executive", technician: "Technician", admin: "Administrator",
};
const general = { title: "Hospital policies", question: "What are the hospital’s infection prevention and hand hygiene guidelines?", icon: "book" as const };
export const PROMPTS: Record<Role, { title: string; question: string; icon: "book" | "heart" | "chart" | "tool" }[]> = {
  doctor: [
    { title: "Clinical guidance", question: "What clinical guidance is available for assessing and managing sepsis?", icon: "heart" },
    { title: "Care coordination", question: "What should be included in a nursing handover?", icon: "book" },
    general,
  ],
  nurse: [
    { title: "Nursing handover", question: "What should be included in a nursing handover?", icon: "heart" },
    { title: "Patient safety", question: "What are the guidelines for preventing patient falls?", icon: "book" },
    general,
  ],
  billing_executive: [
    { title: "Claims overview", question: "How many claims are there for each claim status?", icon: "chart" },
    { title: "Billing guidance", question: "What is the process for handling a rejected insurance claim?", icon: "book" },
    general,
  ],
  billing: [
    { title: "Claims overview", question: "How many claims are there for each claim status?", icon: "chart" },
    { title: "Billing guidance", question: "What is the process for handling a rejected insurance claim?", icon: "book" },
    general,
  ],
  technician: [
    { title: "Equipment checks", question: "What preventive maintenance procedures are documented for hospital equipment?", icon: "tool" },
    { title: "Report an issue", question: "How should an equipment malfunction be reported and escalated?", icon: "book" },
    general,
  ],
  admin: [
    { title: "Claims overview", question: "How many claims are there for each claim status?", icon: "chart" },
    { title: "Maintenance trends", question: "How many maintenance tickets are there by status?", icon: "tool" },
    general,
  ],
};
