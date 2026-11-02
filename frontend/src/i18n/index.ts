/** Public i18n API for features and the shell. See ARCHITECTURE.md "Internationalisation". */
export {
  activateLocale,
  isLocale,
  LOCALES,
  SOURCE_LOCALE,
  type Locale,
} from "./locale";
export { LocaleProvider, useLocale } from "./LocaleProvider";
