package main

import (
	"encoding/json"
	"testing"
)

func TestLegacyHistoryBoundsBytesAndClearsDiscardedSnapshotReferences(t *testing.T) {
	b := newBook()
	for i := 0; i < 10; i++ {
		b.retainEvent(Event{Data: make(json.RawMessage, 3*1024*1024)})
	}
	if b.ringBytes > maxEventHistoryBytes || len(b.ring) > 5 {
		t.Fatal("large snapshots exceeded byte bound")
	}
	// Trimming must clear references in the original backing array as well.
	b.ring = make([]Event, 0, 20)
	b.ringBytes = 0
	for i := 0; i < 10; i++ {
		b.retainEvent(Event{Data: make(json.RawMessage, 3*1024*1024)})
	}
	before := b.ring
	b.retainEvent(Event{Data: make(json.RawMessage, maxEventHistoryBytes+1)})
	if b.ringBytes != 0 || len(b.ring) != 0 {
		t.Fatal("oversized legacy event retained")
	}
	for _, e := range before {
		if e.Data != nil {
			t.Fatal("discarded payload remains in backing array")
		}
	}
}

func TestLegacyHistoryStillBoundsSmallEventsByCount(t *testing.T) {
	b := newBook()
	for i := 0; i < 40000; i++ {
		b.retainEvent(Event{Sequence: uint64(i)})
	}
	if len(b.ring) != 32768 || b.ring[0].Sequence != 7232 {
		t.Fatal("legacy cursor history has wrong bounds")
	}
}
