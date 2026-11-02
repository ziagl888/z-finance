---
order: 950
---

# Questions and answers

Short answers to what people ask first. Every answer points to the chapter that explains it.
Words you do not know are in the [Glossary](glossary.md).

## Installing and starting

**Do I need an account, a subscription or an internet connection?**
No. z-finance runs on your own computer and works offline. The only things that ever reach out are
optional and off until you switch them on: the check for new versions
([Settings](settings.md)) and the receipt agent ([Receipts](receipts.md)).

**Where is my data?**
In one folder that belongs to your Windows account, `%LOCALAPPDATA%\z-finance`. Nothing is sent
anywhere. See [Installing z-finance](install.md).

**Windows says it "protected my PC". Is something wrong?**
No. The program is not signed with a paid certificate. Click **More info → Run anyway**, see
[Installing z-finance](install.md).

**I closed the browser tab. Is z-finance still running?**
Yes, until you choose **Quit** in the menu of the **z** icon in the notification area. Starting it
again only brings the open one back.

**Can I use it on my phone, or from another computer?**
No. z-finance answers only on the computer it runs on and is made for a computer screen.

**Does it run on macOS or Linux?**
The installer is for Windows. On other systems it starts from source; the README describes how.

## The figures

**Why is "free until the end of the month" lower than my bank balance?**
Because it already subtracts what is planned until the end of the month, the reserve you set and
the levies you still owe. It is the money you can spend without a bad surprise. See
[Dashboard](index.md) and [Levy & revenue](levy.md).

**I received 10,000 €. Why did the figure rise by only 7,000 €?**
With a levy rate of 30 %, 3,000 € of it belong to income tax and SVS from the day the money
arrives. z-finance sets them aside at once, so the free figure never includes them.

**Which levy rate do I enter?**
The share of your business income that income tax and social insurance will claim. Ask your tax
adviser; you can change it at any time and everything recalculates. See [Levy & revenue](levy.md).

**Does z-finance do VAT?**
No. It is made for a small business without VAT. Prepayments of income tax and SVS are under
[Taxes & levies](tax.md).

**Does it connect to my bank?**
No. You download a statement from your online banking and load it with [Import](import.md); the
same file twice adds nothing.

**Which currency?**
Euro only. A receipt in another currency is put on the list of receipts to check.

## Bookings and planned items

**I booked the wrong amount. What now?**
Open the booking in the list on [Quick entry](entry.md), correct it and save, or delete it. A
transfer between two of your accounts is always changed as a pair.

**What is the difference between confirming, skipping and postponing?**
_Confirm_ turns a planned item into a real booking. _Skip_ says it does not happen this time; it
counts nowhere and the recurring item goes on. _Postpone_ moves it to a later date. See
[Due items](due.md).

**Why do I see planned items only for the next twelve months?**
z-finance fills up twelve months ahead every time it starts. Older due dates are not created
afterwards. See [Recurring items](recurring.md).

**A payment shows up as income and expense. It was a transfer between my own accounts.**
Book it as a transfer, see [Quick entry](entry.md). A transfer is neither income nor expense; the
owner draw between business and private is one too. See [Month](month.md).

## Money set aside

**What is a pot?**
A part of one account that you have set aside for a goal. The bank balance stays what it is; the
pots say what it is meant for. See [Savings pots](pots.md).

**Why is my deposit into a pot refused?**
Because it is more than the free balance of the account (balance minus everything already in pots).
See [Savings pots](pots.md).

**What does the allocation do?**
It splits an income into shares (levy, debt, pots) according to your rules and proposes it. Nothing
is booked until you confirm. See [Allocation](allocation.md).

## Backup and moving

**How do I back up?**
z-finance saves a copy every time it starts and keeps the newest ten. Under **Backup** you can save
one at any moment and download it as a `.zip` with the receipt photos. Copy that file somewhere
else now and then. See [Backup and restore](backup.md).

**How do I move to a new computer?**
Install z-finance there, open **Backup** before you enter anything — it is in the sidebar even
while the welcome wizard is showing — choose your `.zip` and restore it. A restore works only into
an empty z-finance and never merges two sets of data.

**Can I get my data out?**
Yes: the backup export is plain JSON, and the [Month](month.md) screen exports a month as CSV.

## Help

**Where do I find help?**
The **?** button on every screen explains that screen. This manual is under **User manual** and
has a search box. See [Using the help and the manual](manual.md).

**Something looks wrong.**
Every chapter ends with "Typical problems". If that does not help, open an issue on the project's
page on GitHub. Report a security problem privately, through GitHub's "Report a vulnerability".

If your question is not here, the chapter for the screen you are on is usually faster than
searching this list.
