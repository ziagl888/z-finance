---
order: 950
---

# Fragen und Antworten

Kurze Antworten auf das, was zuerst gefragt wird. Jede Antwort verweist auf das Kapitel, das sie
erklärt. Wörter, die du nicht kennst, stehen im [Glossar](glossary.md).

## Installieren und starten

**Brauche ich ein Konto, ein Abo oder eine Internetverbindung?**
Nein. z-finance läuft auf deinem eigenen Computer und funktioniert offline. Nach außen geht nur,
was optional ist und aus bleibt, bis du es einschaltest: die Suche nach neuen Versionen
([Einstellungen](settings.md)) und der Bon-Agent ([Belege](receipts.md)).

**Wo liegen meine Daten?**
In einem Ordner, der zu deinem Windows-Konto gehört, `%LOCALAPPDATA%\z-finance`. Nichts wird
irgendwohin gesendet. Siehe [z-finance installieren](install.md).

**Windows sagt, es habe meinen PC „geschützt“. Stimmt etwas nicht?**
Nein. Das Programm ist nicht mit einem bezahlten Zertifikat signiert. Klicke auf **Weitere
Informationen → Trotzdem ausführen**, siehe [z-finance installieren](install.md).

**Ich habe den Browser-Tab geschlossen. Läuft z-finance noch?**
Ja, bis du im Menü des **z**-Symbols im Infobereich **Beenden** wählst. Ein zweiter Start holt nur
das laufende z-finance zurück.

**Kann ich es am Handy oder von einem anderen Computer aus benutzen?**
Nein. z-finance antwortet nur auf dem Computer, auf dem es läuft, und ist für einen Computer-Bildschirm
gemacht.

**Läuft es auf macOS oder Linux?**
Der Installer ist für Windows. Auf anderen Systemen startet es aus dem Quellcode; die README
beschreibt, wie.

## Die Zahlen

**Warum ist „frei bis Monatsende“ niedriger als mein Kontostand?**
Weil schon abgezogen ist, was bis Monatsende geplant ist, die Reserve, die du eingestellt hast, und
die Abgaben, die du noch schuldest. Es ist das Geld, das du ausgeben kannst, ohne böse
Überraschung. Siehe [Übersicht](index.md) und [Abgaben & Umsatz](levy.md).

**Ich habe 10.000 € bekommen. Warum steigt die Zahl nur um 7.000 €?**
Bei einem Abgabensatz von 30 % gehören 3.000 € davon ab dem Tag des Eingangs der Einkommensteuer
und der SVS. z-finance legt sie sofort beiseite, deshalb enthält die freie Zahl sie nie.

**Welchen Abgabensatz trage ich ein?**
Den Anteil deiner Geschäftseinnahmen, den Einkommensteuer und Sozialversicherung beanspruchen
werden. Frag deinen Steuerberater; du kannst ihn jederzeit ändern, und alles wird neu gerechnet.
Siehe [Abgaben & Umsatz](levy.md).

**Kennt z-finance die Umsatzsteuer?**
Nein. Es ist für Kleinunternehmer ohne Umsatzsteuer gemacht. Die Vorauszahlungen für
Einkommensteuer und SVS stehen unter [Steuern & Abgaben](tax.md).

**Verbindet es sich mit meiner Bank?**
Nein. Du lädst einen Kontoauszug aus dem Online-Banking und spielst ihn mit [Import](import.md) ein;
dieselbe Datei zweimal fügt nichts hinzu.

**Welche Währung?**
Nur Euro. Ein Beleg in anderer Währung landet in der Liste der zu prüfenden Belege.

## Buchungen und geplante Posten

**Ich habe einen falschen Betrag gebucht. Was jetzt?**
Öffne die Buchung in der Liste der [Schnellerfassung](entry.md), korrigiere sie und speichere, oder
lösche sie. Eine Umbuchung zwischen zwei deiner Konten wird immer als Paar geändert.

**Was ist der Unterschied zwischen bestätigen, überspringen und verschieben?**
_Bestätigen_ macht aus einem geplanten Posten eine echte Buchung. _Überspringen_ sagt, dass er
diesmal nicht stattfindet; er zählt nirgends, und die Dauerbuchung läuft weiter. _Verschieben_ setzt
ihn auf ein späteres Datum. Siehe [Fällige Posten](due.md).

**Warum sehe ich geplante Posten nur für die nächsten zwölf Monate?**
z-finance füllt bei jedem Start zwölf Monate im Voraus auf. Ältere Fälligkeiten werden nicht
nachträglich angelegt. Siehe [Dauerbuchungen](recurring.md).

**Eine Zahlung erscheint als Einnahme und als Ausgabe. Es war eine Umbuchung zwischen meinen Konten.**
Buche sie als Umbuchung, siehe [Schnellerfassung](entry.md). Eine Umbuchung ist weder Einnahme noch
Ausgabe; die Privatentnahme zwischen Geschäft und Privat ist auch eine. Siehe [Monat](month.md).

## Beiseitegelegtes Geld

**Was ist ein Topf?**
Ein Teil eines Kontos, den du für ein Ziel beiseitegelegt hast. Der Kontostand bleibt, wie er ist;
die Töpfe sagen, wofür er gedacht ist. Siehe [Spartöpfe](pots.md).

**Warum wird meine Einzahlung in einen Topf abgelehnt?**
Weil sie höher ist als der freie Saldo des Kontos (Kontostand minus alles, was schon in Töpfen
liegt). Siehe [Spartöpfe](pots.md).

**Was macht die Zuteilung?**
Sie teilt einen Eingang nach deinen Regeln in Anteile (Abgaben, Schuld, Töpfe) und schlägt das vor.
Gebucht wird erst, wenn du bestätigst. Siehe [Zuteilung](allocation.md).

## Backup und Umzug

**Wie sichere ich?**
z-finance speichert bei jedem Start eine Kopie und behält die neuesten zehn. Unter **Datensicherung**
speicherst du jederzeit eine weitere und lädst sie als `.zip` mit den Beleg-Fotos herunter.
Kopiere diese Datei ab und zu an einen anderen Ort. Siehe [Sichern und Wiederherstellen](backup.md).

**Wie ziehe ich auf einen neuen Computer um?**
Installiere z-finance dort, öffne **Datensicherung**, bevor du etwas einträgst — es steht in der
Seitenleiste, auch solange der Willkommens-Assistent zu sehen ist —, wähle dein `.zip` und stelle es
wieder her. Eine Wiederherstellung geht nur in ein leeres z-finance und führt nie zwei Datenbestände
zusammen.

**Kann ich meine Daten herausholen?**
Ja: der Backup-Export ist einfaches JSON, und der Bildschirm [Monat](month.md) exportiert einen
Monat als CSV.

## Hilfe

**Wo finde ich Hilfe?**
Der **?**-Knopf auf jedem Bildschirm erklärt diesen Bildschirm. Dieses Handbuch steht unter
**Handbuch** und hat ein Suchfeld. Siehe [Hilfe und Handbuch benutzen](manual.md).

**Etwas sieht falsch aus.**
Jedes Kapitel endet mit „Typische Probleme“. Hilft das nicht, eröffne ein Issue auf der
Projektseite bei GitHub. Ein Sicherheitsproblem meldest du privat, über GitHubs „Report a
vulnerability“.

Steht deine Frage nicht hier, ist meist das Kapitel zum Bildschirm, auf dem du bist, schneller als
diese Liste zu durchsuchen.
