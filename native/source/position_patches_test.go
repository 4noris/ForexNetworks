package main

import (
	"encoding/json"
	"strconv"
	"testing"
)

func patchFrame(raw string) StateChanges {
	return StateChanges{Updates: map[string]map[string]json.RawMessage{"positions": {"1": json.RawMessage(raw)}}}
}

func TestPositionPatchesPreserveFullBaselineThenSendOnlyChangedFields(t *testing.T) {
	b := newPositionBaselines()
	first := patchFrame(`{"Ticket":"1","Login":7,"Symbol":"TEST_ONLY","Profit":1.25,"PriceCurrent":1.5}`)
	b.compact(&first)
	if first.Updates["positions"]["1"] == nil || first.Patches != nil {
		t.Fatal("first row must be complete")
	}
	next := patchFrame(`{"Ticket":"1","Login":7,"Symbol":"TEST_ONLY","Profit":-5.25,"PriceCurrent":1.51}`)
	b.compact(&next)
	fields := next.Patches["positions"]["1"]
	if len(fields) != 2 || string(fields["Profit"]) != "-5.25" || string(fields["PriceCurrent"]) != "1.51" {
		t.Fatal("incorrect financial field patch")
	}
	if _, exists := next.Updates["positions"]["1"]; exists {
		t.Fatal("patched position also sent as replacement")
	}
	closed := StateChanges{Updates: map[string]map[string]json.RawMessage{"positions": {"1": nil}}}
	b.compact(&closed)
	if len(b.rows) != 0 || closed.Updates["positions"]["1"] != nil {
		t.Fatal("delete did not clear baseline")
	}
	reopened := patchFrame(`{"Ticket":"1","Login":7,"Symbol":"TEST_ONLY","Profit":0,"PriceCurrent":1.5}`)
	b.compact(&reopened)
	if reopened.Patches != nil {
		t.Fatal("reused ticket needs a complete baseline")
	}
}

func TestPositionSchemaChangesAreFullReplacements(t *testing.T) {
	b := newPositionBaselines()
	first := patchFrame(`{"Ticket":"1","Profit":1,"OldField":2}`)
	b.compact(&first)
	next := patchFrame(`{"Ticket":"1","Profit":3,"NewField":2}`)
	b.compact(&next)
	if next.Patches != nil || next.Updates["positions"]["1"] == nil {
		t.Fatal("schema change incorrectly patched")
	}
}

func TestPositionBaselineMemoryIsBoundedWithoutDroppingUpdates(t *testing.T) {
	b := newPositionBaselines()
	rows := make(map[string]json.RawMessage)
	for i := 0; i < 40000; i++ {
		rows[strconv.Itoa(i)] = json.RawMessage(`{"Ticket":"` + strconv.Itoa(i) + `","Profit":1}`)
	}
	frame := StateChanges{Updates: map[string]map[string]json.RawMessage{"positions": rows}}
	b.compact(&frame)
	if len(b.rows) > 32768 || b.bytes > 32*1024*1024 || len(frame.Updates["positions"]) != 40000 {
		t.Fatal("baseline eviction lost a row or exceeded limits")
	}
}
