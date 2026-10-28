package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"strconv"
	"testing"
	"time"
)

func TestCompressedStateFrameFlushesBeforeAcknowledgement(t *testing.T) {
	b := readyBook(t)
	server := httptest.NewServer(http.HandlerFunc(b.changes))
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, "GET", server.URL+"?epoch="+b.Epoch+"&after="+strconv.FormatUint(b.Sequence, 10), nil)
	request.Header.Set("Accept-Encoding", "gzip")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.Header.Get("Content-Encoding") != "gzip" {
		t.Fatal("compression not enabled")
	}
	reader, err := gzip.NewReader(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	var frame StateChanges
	if err := json.NewDecoder(reader).Decode(&frame); err != nil {
		t.Fatal(err)
	}
	if frame.Stream == "" || frame.Sequence != b.Sequence {
		t.Fatal("compressed state not decoded")
	}
}

func TestPythonReadsCompressedGoStream(t *testing.T) {
	python := os.Getenv("RMS_TEST_PYTHON")
	if python == "" {
		t.Skip("optional cross-language stream check")
	}
	b := readyBook(t)
	after := b.Sequence
	for i := 1; i <= 10000; i++ {
		_ = b.apply(event("positions", "upsert", strconv.Itoa(i), `{"Ticket":"`+strconv.Itoa(i)+`","Login":7,"Symbol":"TEST_ONLY","Profit":-12345.67,"Comment":"large stream test fixture large stream test fixture large stream test fixture"}`))
	}
	server := httptest.NewServer(http.HandlerFunc(b.changes))
	defer server.Close()
	url := server.URL + "?epoch=" + b.Epoch + "&after=" + strconv.FormatUint(after, 10)
	script := "import json,sys\nfrom urllib.request import urlopen,Request\nsys.path.insert(0,sys.argv[2])\nfrom services.native_bridge import CompressedStateStream\nwith urlopen(Request(sys.argv[1],headers={'Accept-Encoding':'gzip'}),timeout=2) as r:\n data=json.loads(CompressedStateStream(r).readline(64*1024*1024))\n assert len(data['updates']['positions'])==10000\n"
	output, err := exec.Command(python, "-c", script, url, os.Getenv("RMS_TEST_ROOT")).CombinedOutput()
	if err != nil {
		t.Fatalf("Python could not read compressed Go frame: %v %s", err, output)
	}
}

type frameWriter struct {
	header http.Header
	frames chan []byte
}

func (w *frameWriter) Header() http.Header { return w.header }
func (w *frameWriter) WriteHeader(int)     {}
func (w *frameWriter) Flush()              {}
func (w *frameWriter) Write(data []byte) (int, error) {
	w.frames <- append([]byte(nil), data...)
	return len(data), nil
}

func TestStateStreamAllowsOnlyOneUnacknowledgedBatch(t *testing.T) {
	b := readyBook(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	w := &frameWriter{header: make(http.Header), frames: make(chan []byte, 4)}
	r := httptest.NewRequest("GET", "/internal/native/changes?epoch="+b.Epoch+"&after="+strconv.FormatUint(b.Sequence, 10), nil).WithContext(ctx)
	done := make(chan struct{})
	go func() { b.changes(w, r); close(done) }()
	var first StateChanges
	select {
	case raw := <-w.frames:
		_ = json.Unmarshal(raw, &first)
	case <-time.After(time.Second):
		t.Fatal("missing first batch")
	}
	for i := 0; i < 1000; i++ {
		_ = b.apply(event("ticks", "upsert", "EURUSD", `{"Symbol":"EURUSD","Bid":1.2}`))
	}
	select {
	case <-w.frames:
		t.Fatal("unacknowledged state queued in socket")
	case <-time.After(150 * time.Millisecond):
	}
	send := func(sequence uint64) int {
		data, _ := json.Marshal(map[string]any{"stream_id": first.Stream, "sequence": sequence})
		ack := httptest.NewRecorder()
		b.acknowledgeChanges(ack, httptest.NewRequest("POST", "/internal/native/ack", bytes.NewReader(data)))
		return ack.Code
	}
	if send(first.Sequence+1) != 409 {
		t.Fatal("wrong acknowledgement accepted")
	}
	if send(first.Sequence) != 204 {
		t.Fatal("correct acknowledgement rejected")
	}
	var next StateChanges
	select {
	case raw := <-w.frames:
		_ = json.Unmarshal(raw, &next)
	case <-time.After(time.Second):
		t.Fatal("acknowledgement did not resume stream")
	}
	if next.Base != first.Sequence || next.Sequence != b.Sequence || len(next.Updates["ticks"]) != 1 {
		t.Fatal("reader did not catch the latest state")
	}
	cancel()
	<-done
}

func readyBook(t *testing.T) *Book {
	b := newBook()
	_ = b.apply(event("status", "reset", "", `{}`))
	_ = b.apply(event("users", "upsert", "7", `{"Login":7}`))
	_ = b.apply(event("accounts", "upsert", "7", `{"Login":7,"Equity":100}`))
	_ = b.apply(event("positions", "replace", "", `[]`))
	_ = b.apply(event("accounts", "cycle_end", "", `{}`))
	if err := b.apply(event("status", "snapshot_end", "", `{"connected":true}`)); err != nil {
		t.Fatal(err)
	}
	return b
}

func TestStateReaderCatchesLatestAfterMoreEventsThanRawRing(t *testing.T) {
	b := readyBook(t)
	after := b.Sequence
	for i := 0; i < 100000; i++ {
		_ = b.apply(event("ticks", "upsert", "EURUSD", `{"Symbol":"EURUSD","Bid":1.1}`))
	}
	_ = b.apply(event("ticks", "upsert", "EURUSD", `{"Symbol":"EURUSD","Bid":1.2}`))
	batch, err := b.stateChanges(b.Epoch, after)
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.Updates["ticks"]) != 1 || batch.Base != after || batch.Sequence != b.Sequence {
		t.Fatal("latest-state replication must not retain every tick or require a resnapshot")
	}
	var row struct{ Bid float64 }
	_ = json.Unmarshal(batch.Updates["ticks"]["EURUSD"], &row)
	if row.Bid != 1.2 {
		t.Fatal("latest quote was lost")
	}
}

func TestStateDeletesReplacementsAndCleanLoginStayExact(t *testing.T) {
	b := readyBook(t)
	_ = b.apply(event("positions", "replace", "", `[{"Ticket":"1","Login":7},{"Ticket":"2","Login":8}]`))
	after := b.Sequence
	_ = b.apply(event("positions", "delete", "1", `null`))
	_ = b.apply(event("positions", "upsert", "3", `{"Ticket":"3","Login":8}`))
	_ = b.apply(event("positions", "clean_login", "8", `null`))
	batch, err := b.stateChanges(b.Epoch, after)
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"1", "2", "3"} {
		row, exists := batch.Updates["positions"][id]
		if !exists || row != nil {
			t.Fatal("missing closed-position tombstone", id)
		}
	}
	_ = b.apply(event("positions", "replace", "", `[{"Ticket":"9","Login":7}]`))
	after = b.Sequence
	_ = b.apply(event("positions", "replace", "", `[]`))
	batch, err = b.stateChanges(b.Epoch, after)
	row, exists := batch.Updates["positions"]["9"]
	if err != nil || !exists || row != nil {
		t.Fatal("replacement failed to remove previous position")
	}
}

func TestStateReplicationRejectsResetAndBoundsDeletionHistory(t *testing.T) {
	b := readyBook(t)
	epoch, after := b.Epoch, b.Sequence
	// Unique tombstones are bounded; a reader older than pruned history resets.
	b.Lock()
	for i := uint64(1); i < 70000; i++ {
		b.Sequence++
		b.markChanged("positions", stringID(i), b.Sequence)
	}
	b.Unlock()
	if len(b.deleted) > 65536 {
		t.Fatal("unbounded tombstones")
	}
	if _, err := b.stateChanges(epoch, after); err == nil {
		t.Fatal("pruned deletion history accepted")
	}
	_ = b.apply(event("status", "reset", "", `{}`))
	if _, err := b.stateChanges(epoch, b.Sequence); err == nil {
		t.Fatal("old epoch accepted")
	}
}

func stringID(i uint64) string { return strconv.FormatUint(i, 10) }

func TestSlowSnapshotEncodingDoesNotBlockMT5Producer(t *testing.T) {
	b := readyBook(t)
	w := &pausedStream{header: make(http.Header), started: make(chan struct{}), release: make(chan struct{})}
	done := make(chan struct{})
	go func() { b.snapshot(w, httptest.NewRequest("GET", "/internal/native/snapshot", nil)); close(done) }()
	<-w.started
	applied := make(chan struct{})
	go func() { _ = b.apply(event("accounts", "upsert", "7", `{"Login":7,"Equity":200}`)); close(applied) }()
	select {
	case <-applied:
	case <-time.After(time.Second):
		close(w.release)
		<-done
		t.Fatal("snapshot response held the producer lock")
	}
	close(w.release)
	<-done
}
