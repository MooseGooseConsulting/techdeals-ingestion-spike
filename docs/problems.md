# Problems this project must solve

Status: draft, 2026-10-16. This records the problems and required behavior
discussed with the owner. It is not a claim that they have been solved.

## Purpose

We have heterogeneous hardware information that needs to become useful for
sorting, querying, tallying, and buying analysis by other agents.

The difficult part is preserving its meaning from the original source through
to the data retrieved by a consuming agent. The intended result is a functioning
ingestion pipeline and database whose demonstrated improvements can enhance
the main TechDeals project.

Other agents are the database consumers. They need queryable facts,
relationships, quantities, price scope, provenance, history, and explicit
unknowns to support their own tasks. The owner is not expected to query the
database personally. A fixed menu of human-facing buying questions does not
define the scope. The data must be understandable from its documented structure
and meanings without depending on the ingestion agent's conversation history.

The [implementation plan](plan.md) describes how we propose to address these
problems. Its sequence and technical choices can change as we learn; the
required outcomes below remain the work to accomplish.

## P1. Heterogeneous information

A standalone part, a configured server, a lot, and a multi-item sale post
describe hardware and purchases differently. Flattening them into the same
record can erase the differences needed to understand or compare them.

**Required result:** other agents can retrieve, sort, query, and tally useful
information from these different structures together while retaining the context
that determines what each item and purchase means. This must work across varied
real source and hardware shapes; one server family cannot establish that.

## P2. Contents, quantities, and purchase structure

One URL may contain several independent items, a configured system, a lot, or
selectable configurations. Installed, included, optional, compatible, mentioned,
and explicitly absent hardware have different meanings. Components inside a
system are not necessarily available for separate purchase.

Counts and units can also change meaning during interpretation: six 600GB
drives are six drives of 600GB each, with 3600GB aggregate capacity. Supported
memory capacity is not installed memory. Different adapters must remain distinct.

**Required result:** stored facts and relationships identify what a purchase
includes, each component's attributes and quantity, what is absent or optional,
and which items can actually be bought separately. A consuming agent can query
these distinctions without reconstructing them from the raw listing each time.

## P3. What prices cover and how costs are apportioned

An amount may buy one item, several identical units, a mixed lot, a complete
system, or a particular selectable configuration. A base price cannot describe
upgraded contents. Asks, bids, sold prices, and acquisition costs are different
facts; currencies and purchase conditions also affect their meaning.

Breaking down a system's cost can be useful for analysis, but an attributed
component cost is not evidence that the seller offers that component at that
price.

**Required result:** each stated amount remains attached to what it actually
buys, including quantity, currency, price kind, and relevant conditions. Cost
apportionment has an explicit method and supporting evidence, preserves the
original purchase amount, and exposes missing support or uncertainty. Allocated
costs remain distinguishable from stated market prices.

## P4. Meaningful matching and comparison

Similar names, a shared chassis, or a shared CPU do not establish equivalent
configurations. Missing discriminating facts can make a plausible match
misleading. The attributes that matter depend on the hardware and the consuming
agent's task.

**Required result:** consuming agents can query purchases against their own
requirements and exclude misleading near matches. The data preserves differences
in installed configuration, condition, purchase quantity, and other relevant constraints.
Incomplete identity remains explicit rather than becoming a guessed match.

## P5. Source evidence, dates, and history

Every selected fact or price needs a traceable source. Fetch time, publication
time, import time, and the time a price was observed describe different events.
Reinterpreting a capture is also different from observing a real market change.

**Required result:** agent queries expose the supporting source and known
time context. Missing dates stay unknown. Repeated captures and revised
interpretations preserve price and availability history without duplicating
purchases or attaching old prices to different components. Consuming agents can
distinguish historical evidence, current observations, and unavailable or unknown inventory.

## P6. Useful incomplete information and visible failures

Missing identity, an unknown fetch date, an ambiguous price, conflicting claims,
or a failed item interpretation must not make the remaining useful information
disappear. A successful model request does not establish a usable interpretation.

**Required result:** original evidence and useful partial interpretations remain
retrievable. Unknowns, conflicts, failures, and exclusions have explicit
dispositions. Consuming agents can retrieve known information and the limits on
using it; no missing fact is silently invented to make a record fit.

## P7. Categories that develop with the information

Hardware types and useful groupings develop as new information arrives. A fixed
taxonomy or one source's field layout can prevent the next useful category
from being represented or queried.

**Required result:** new hardware and meaningful categories can be organized and
queried while preserving their relevant attributes and relationships. The
database supports category-specific requirements without assuming a legacy
schema or forcing unlike configurations into the same comparison.

## Working boundaries

Use batched free-model parsing. Work with the existing scrapers' captured
evidence; broad legacy migration waits until the new path works. Development
must protect the existing cluster and production data. Production incorporation
follows demonstration of useful behavior and review of the affected consumers
and history.
