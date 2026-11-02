/** Help framework: help button + panel, manual page, embedded chapters. See ARCHITECTURE.md "Help and manual". */
import "./help.css";

export { Handbook } from "./Handbook";
export { HelpButton } from "./HelpButton";
export { MANUAL_PATH, ManualPage, manualHref } from "./ManualPage";
export { chapterForPath, manual, type Chapter } from "./manual";
