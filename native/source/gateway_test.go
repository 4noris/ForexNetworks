package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

type pausedStream struct {
	header  http.Header
	started chan struct{}
	release chan struct{}
	once    sync.Once
}

func (w *pausedStream) Header() http.Header { return w.header }
func (w *pausedStream) WriteHeader(int)     {}
func (w *pausedStream) Flush()              {}
func (w *pausedStream) Write(p []byte) (int, error) {
	w.once.Do(func() { close(w.started); <-w.release })
	return len(p), nil
}

func TestNativeStreamAbsorbsAccountBurstDuringConsumerPublish(t *testing.T) {
	b := newBook()
	ctx, cancel := context.WithCancel(context.Background())
	w := &pausedStream{header: make(http.Header), started: make(chan struct{}), release: make(chan struct{})}
	r := httptest.NewRequest("GET", "/internal/native/events?epoch="+b.Epoch+"&after=0", nil).WithContext(ctx)
	done := make(chan struct{})
	go func() { b.events(w, r); close(done) }()
	select {
	case <-w.started:
	case <-time.After(time.Second):
		cancel()
		t.Fatal("stream did not start")
	}
	for i := 0; i < 4096; i++ {
		_ = b.apply(event("accounts", "upsert", "7", `{"Login":7,"Equity":100}`))
	}
	b.RLock()
	retained := len(b.subscribers)
	b.RUnlock()
	cancel()
	close(w.release)
	<-done
	if retained != 1 {
		t.Fatal("normal account burst disconnected a temporarily busy consumer")
	}
}

func event(kind, op, id, data string) Event {
	return Event{Kind: kind, Op: op, ID: id, Data: json.RawMessage(data), Received: time.Now().UnixMilli()}
}
func TestBookRequiresCompleteAuthoritativeAccountCoverage(t *testing.T) {
	b := newBook()
	if err := b.apply(event("status", "reset", "", `{}`)); err != nil {
		t.Fatal(err)
	}
	_ = b.apply(event("users", "upsert", "7", `{"Login":7,"Name":"real"}`))
	if err := b.apply(event("status", "snapshot_end", "", `{"connected":true}`)); err != nil {
		t.Fatal("pending account coverage should wait without restarting bootstrap", err)
	}
	if b.health()["ready"] == true {
		t.Fatal("missing account must not look complete")
	}
	_ = b.apply(event("accounts", "upsert", "7", `{"Login":7,"Equity":100}`))
	_ = b.apply(event("positions", "replace", "", `[]`))
	_ = b.apply(event("accounts", "cycle_end", "", `{}`))
	if err := b.apply(event("status", "snapshot_end", "", `{"connected":true}`)); err != nil {
		t.Fatal(err)
	}
	if b.health()["ready"] != true {
		t.Fatal("complete source not ready")
	}
	b.disconnect()
	if b.health()["ready"] == true {
		t.Fatal("disconnected source looks live")
	}
}

func TestBootstrapWaitsForUsersAddedDuringAccountRead(t *testing.T) {
	b := newBook()
	_ = b.apply(event("status", "reset", "", `{}`))
	_ = b.apply(event("users", "upsert", "7", `{"Login":7}`))
	_ = b.apply(event("accounts", "upsert", "7", `{"Login":7,"Equity":100}`))
	_ = b.apply(event("positions", "replace", "", `[]`))
	_ = b.apply(event("accounts", "cycle_end", "", `{}`))
	_ = b.apply(event("users", "upsert", "8", `{"Login":8}`))
	epoch := b.Epoch
	if err := b.apply(event("status", "snapshot_end", "", `{"connected":true}`)); err != nil {
		t.Fatal(err)
	}
	if b.health()["ready"] == true {
		t.Fatal("uncovered account must not appear live")
	}
	_ = b.apply(event("accounts", "upsert", "8", `{"Login":8,"Equity":20}`))
	_ = b.apply(event("accounts", "cycle_end", "", `{}`))
	if b.health()["ready"] != true || b.Epoch != epoch {
		t.Fatal("pending account metrics should complete the original bootstrap")
	}
	_ = b.apply(event("status", "reset", "", `{}`))
	_ = b.apply(event("accounts", "cycle_end", "", `{}`))
	if b.Complete {
		t.Fatal("a reset requires another snapshot end")
	}
}

func TestDeletedBootstrapUserDoesNotPreventCoverage(t *testing.T) {
	b := newBook()
	_ = b.apply(event("status", "reset", "", `{}`))
	_ = b.apply(event("users", "upsert", "7", `{"Login":7}`))
	_ = b.apply(event("positions", "replace", "", `[]`))
	_ = b.apply(event("accounts", "cycle_end", "", `{}`))
	_ = b.apply(event("status", "snapshot_end", "", `{"connected":true}`))
	_ = b.apply(event("users", "delete", "7", `null`))
	if b.health()["ready"] != true {
		t.Fatal("a deleted account should no longer require metrics")
	}
}
func TestPositionReplacementAndDeletionRemoveClosedTrades(t *testing.T) {
	b := newBook()
	_ = b.apply(event("positions", "replace", "", `[{"Ticket":"9007199254740993","Login":7,"Profit":1}]`))
	_ = b.apply(event("positions", "delete", "9007199254740993", `null`))
	if len(b.Records["positions"]) != 0 {
		t.Fatal("closed ticket retained")
	}
	if err := b.apply(event("positions", "replace", "", `[{"Ticket":"1"},{"Ticket":"1"}]`)); err == nil {
		t.Fatal("duplicate snapshot accepted")
	}
}
func TestSnapshotSequenceAndInternalAuthentication(t *testing.T) {
	b := newBook()
	_ = b.apply(event("groups", "upsert", "real", `{"Group":"real","Currency":"USD"}`))
	handler := secure("private-token", b.snapshot)
	for _, test := range []struct {
		address, token string
		status         int
	}{{"192.0.2.1:12", "private-token", 403}, {"127.0.0.1:12", "wrong", 403}, {"127.0.0.1:12", "private-token", 200}} {
		r := httptest.NewRequest(http.MethodGet, "/internal/native/snapshot", nil)
		r.RemoteAddr = test.address
		r.Header.Set("Authorization", "Bearer "+test.token)
		w := httptest.NewRecorder()
		handler(w, r)
		if w.Code != test.status {
			t.Fatalf("got %d want %d", w.Code, test.status)
		}
	}
}
func TestReconnectionEpochInvalidatesOldSequence(t *testing.T) {
	b := newBook()
	epoch := b.Epoch
	_ = b.apply(event("status", "reset", "", `{}`))
	if epoch == b.Epoch {
		t.Fatal("reset reused epoch")
	}
	r := httptest.NewRequest("GET", "/internal/native/events?epoch="+epoch+"&after=0", nil)
	w := httptest.NewRecorder()
	b.events(w, r)
	if w.Code != 409 {
		t.Fatal("old epoch accepted")
	}
}

func TestOldSnapshotCannotResurrectAClosedPosition(t *testing.T) {
	b := newBook()
	first := event("positions", "upsert", "1", `{"Ticket":"1","Login":7,"Profit":3}`)
	first.Received = 1000
	_ = b.apply(first)
	closed := event("positions", "delete", "1", `null`)
	closed.Received = 1200
	_ = b.apply(closed)
	snapshot := event("positions", "replace", "", `[{"Ticket":"1","Login":7,"Profit":3}]`)
	snapshot.SnapshotStarted = 1100
	snapshot.Received = 1300
	if err := b.apply(snapshot); err != nil {
		t.Fatal(err)
	}
	if len(b.Records["positions"]) != 0 {
		t.Fatal("an older in-flight snapshot resurrected a closed position")
	}
	var emitted []json.RawMessage
	if err := json.Unmarshal(b.ring[len(b.ring)-1].Data, &emitted); err != nil || len(emitted) != 0 {
		t.Fatal("downstream snapshot did not preserve the deletion")
	}
}
