import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Folder,
  FolderOpen,
  Search,
  Users,
  X,
} from "lucide-react";
import { cn } from "../lib/utils";
import { BOOK_CONFIG_EVENT, getBookGroupSettings } from "../lib/api";

const BOOK_OPTIONS = [
  ["a_book", "A Book"],
  ["b_book", "B Book"],
  ["cent", "Cent"],
  ["hybrid", "Hybrid"],
];

/* ── helpers (unchanged logic) ──────────────────────────── */
function splitGroupPath(group) {
  return String(group || "")
    .split(/[\\/]+/)
    .map((p) => p.trim())
    .filter(Boolean);
}
function formatCount(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}
function buildGroupTree(groups) {
  const root = {
    key: "__root__",
    label: "",
    path: "",
    values: [],
    allValues: [],
    children: [],
    childMap: new Map(),
  };
  const uniqueGroups = Array.from(
    new Set((groups || []).map((g) => String(g).trim()).filter(Boolean)),
  );
  uniqueGroups.forEach((group) => {
    const parts = splitGroupPath(group);
    if (parts.length === 0) return;
    let node = root;
    const pathParts = [];
    parts.forEach((part) => {
      pathParts.push(part);
      const childKey = pathParts.join("\0");
      if (!node.childMap.has(childKey)) {
        const child = {
          key: childKey,
          label: part,
          path: pathParts.join("\\"),
          values: [],
          allValues: [],
          children: [],
          childMap: new Map(),
        };
        node.childMap.set(childKey, child);
        node.children.push(child);
      }
      node = node.childMap.get(childKey);
    });
    node.values.push(group);
  });
  const sortAndStrip = (nodes) => {
    nodes.sort((a, b) =>
      a.label.localeCompare(b.label, undefined, {
        numeric: true,
        sensitivity: "base",
      }),
    );
    nodes.forEach((node) => {
      sortAndStrip(node.children);
      node.allValues = Array.from(
        new Set([...node.values, ...node.children.flatMap((c) => c.allValues)]),
      );
      delete node.childMap;
    });
  };
  sortAndStrip(root.children);
  return root.children;
}
function filterTree(nodes, query) {
  if (!query) return nodes;
  const filtered = [];
  nodes.forEach((node) => {
    const nextChildren = filterTree(node.children, query);
    const haystack =
      `${node.label} ${node.path} ${node.values.join(" ")}`.toLowerCase();
    if (haystack.includes(query) || nextChildren.length > 0) {
      filtered.push({ ...node, children: nextChildren });
    }
  });
  return filtered;
}

/* ── TreeNode ───────────────────────────────────────────── */
function TreeNode({
  node,
  level,
  selectedSet,
  openKeys,
  onToggleOpen,
  onToggleValue,
  forceOpen = false,
}) {
  const nodeValues = node.allValues || node.values || [];
  const selectable = nodeValues.length > 0;
  const selectedNodeValues = nodeValues.filter((v) => selectedSet.has(v));
  const isSelected =
    selectable && selectedNodeValues.length === nodeValues.length;
  const isPartial = selectable && selectedNodeValues.length > 0 && !isSelected;
  const hasChildren = node.children.length > 0;
  const isExpanded = forceOpen || openKeys.has(node.key);
  const checkboxRef = useRef(null);

  useEffect(() => {
    if (checkboxRef.current) checkboxRef.current.indeterminate = isPartial;
  }, [isPartial]);

  return (
    <div className="select-none">
      <div
        className="overflow-hidden rounded-xl transition-all duration-150"
        style={{
          border: isSelected
            ? "1px solid rgb(var(--color-accent)/0.3)"
            : isPartial
              ? "1px solid rgb(var(--color-accent)/0.15)"
              : "1px solid var(--inp-border)",
          background: isSelected
            ? "rgb(var(--color-accent)/0.06)"
            : isPartial
              ? "rgb(var(--color-accent)/0.03)"
              : "var(--inp-bg)",
        }}
      >
        {/* left accent bar */}
        <div
          className="absolute left-0 top-0 bottom-0 w-0.5 rounded-l-xl"
          style={{
            background: isSelected
              ? "rgb(var(--color-accent)/0.7)"
              : isPartial
                ? "rgb(var(--color-accent)/0.35)"
                : "transparent",
          }}
        />

        <div className="relative flex items-center gap-2 px-2.5 py-2">
          {/* expand/collapse */}
          <button
            type="button"
            onClick={() => hasChildren && onToggleOpen(node.key)}
            className={cn(
              "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-lg transition-colors",
              hasChildren ? "cursor-pointer" : "pointer-events-none opacity-0",
            )}
            style={{
              background: "var(--btn-ghost-bg)",
              border: "1px solid var(--inp-border)",
              color: "rgb(var(--color-text-3))",
            }}
          >
            {hasChildren &&
              (isExpanded ? (
                <ChevronDown size={11} />
              ) : (
                <ChevronRight size={11} />
              ))}
          </button>

          {/* folder / users icon */}
          <span
            className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg"
            style={{
              background: "var(--btn-ghost-bg)",
              border: "1px solid var(--inp-border)",
              color: "rgb(var(--color-text-2))",
            }}
          >
            {hasChildren ? (
              isExpanded ? (
                <FolderOpen size={13} />
              ) : (
                <Folder size={13} />
              )
            ) : (
              <Users size={13} />
            )}
          </span>

          {/* label */}
          <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
            <span className="min-w-0 flex-1">
              <span
                className="block truncate text-xs font-medium"
                style={{ color: "rgb(var(--color-text))" }}
                title={node.path}
              >
                {node.label}
              </span>
              <span
                className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px]"
                style={{ color: "rgb(var(--color-text-3))" }}
              >
                <span
                  className="rounded-full px-1.5 py-0"
                  style={{
                    background: "var(--btn-ghost-hover)",
                    border: "1px solid var(--inp-border)",
                  }}
                >
                  {formatCount(nodeValues.length, "group")}
                </span>
                {hasChildren && (
                  <span
                    className="rounded-full px-1.5 py-0"
                    style={{
                      background: "var(--btn-ghost-hover)",
                      border: "1px solid var(--inp-border)",
                    }}
                  >
                    {formatCount(node.children.length, "branch", "branches")}
                  </span>
                )}
                {isPartial && (
                  <span
                    className="rounded-full px-1.5 py-0 text-success"
                    style={{
                      background: "rgba(16,185,129,0.08)",
                      border: "1px solid rgba(16,185,129,0.2)",
                    }}
                  >
                    {selectedNodeValues.length}/{nodeValues.length} sel.
                  </span>
                )}
                {isSelected && (
                  <span
                    className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0 text-success"
                    style={{
                      background: "rgba(16,185,129,0.08)",
                      border: "1px solid rgba(16,185,129,0.2)",
                    }}
                  >
                    <Check size={9} /> All
                  </span>
                )}
              </span>
            </span>

            {selectable && (
              <input
                ref={checkboxRef}
                type="checkbox"
                checked={isSelected}
                onChange={() => onToggleValue(nodeValues)}
                onClick={(e) => e.stopPropagation()}
                className="h-3.5 w-3.5 flex-shrink-0 rounded"
                style={{ accentColor: "rgb(var(--color-accent))" }}
              />
            )}
          </label>
        </div>
      </div>

      {hasChildren && isExpanded && (
        <div
          className="ml-4 mt-1.5 space-y-1.5 border-l pl-3"
          style={{ borderColor: "var(--inp-border)", borderStyle: "dashed" }}
        >
          {node.children.map((child) => (
            <TreeNode
              key={child.key}
              node={child}
              level={level + 1}
              selectedSet={selectedSet}
              openKeys={openKeys}
              onToggleOpen={onToggleOpen}
              onToggleValue={onToggleValue}
              forceOpen={forceOpen}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── GroupTreeSelect ─────────────────────────────────────── */
export default function GroupTreeSelect({
  groups = [],
  value = [],
  onChange,
  className = "",
  label = "Group Tree",
  showBookPresets = true,
  emptyMeansAll = true,
  replaceOnSelect = false,
}) {
  const wrapperRef = useRef(null);
  const dropdownRef = useRef(null);
  const searchRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [manualOpenKeys, setManualOpenKeys] = useState(() => new Set());
  const [bookSettings, setBookSettings] = useState(null);
  const [dropdownStyle, setDropdownStyle] = useState({});

  const allGroups = useMemo(
    () =>
      Array.from(
        new Set((groups || []).map((g) => String(g).trim()).filter(Boolean)),
      ),
    [groups],
  );
  const tree = useMemo(() => buildGroupTree(allGroups), [allGroups]);
  const selectedValues = useMemo(
    () =>
      Array.from(
        new Set((value || []).map((i) => String(i).trim()).filter(Boolean)),
      ),
    [value],
  );
  const selectedSet = useMemo(() => new Set(selectedValues), [selectedValues]);
  const selectedVisibleValues = useMemo(
    () => allGroups.filter((g) => selectedSet.has(g)),
    [allGroups, selectedSet],
  );
  const filteredTree = useMemo(
    () => filterTree(tree, search.trim().toLowerCase()),
    [tree, search],
  );
  const queryActive = search.trim().length > 0;
  const selectedCount = selectedVisibleValues.length;

  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!showBookPresets) return undefined;
    let active = true;
    getBookGroupSettings()
      .then((settings) => {
        if (active) setBookSettings(settings);
      })
      .catch(() => {});
    const handleBooksChanged = (event) => setBookSettings(event.detail);
    window.addEventListener(BOOK_CONFIG_EVENT, handleBooksChanged);
    return () => {
      active = false;
      window.removeEventListener(BOOK_CONFIG_EVENT, handleBooksChanged);
    };
  }, [showBookPresets]);

  useEffect(() => {
    if (!open) return undefined;
    const positionDropdown = () => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(Math.max(rect.width, 420), window.innerWidth - 24);
      const left = Math.min(
        Math.max(12, rect.left),
        window.innerWidth - width - 12,
      );
      setDropdownStyle({ left, top: rect.bottom + 6, width });
    };
    positionDropdown();
    const onPointer = (e) => {
      if (
        wrapperRef.current &&
        !wrapperRef.current.contains(e.target) &&
        !dropdownRef.current?.contains(e.target)
      )
        setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", positionDropdown);
    window.addEventListener("scroll", positionDropdown, true);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", positionDropdown);
      window.removeEventListener("scroll", positionDropdown, true);
    };
  }, [open]);

  const toggleGroupValues = (values) => {
    if (replaceOnSelect) {
      const allSelected = values.every((group) => selectedSet.has(group));
      const replacement = allSelected ? new Set() : new Set(values);
      onChange?.(allGroups.filter((group) => replacement.has(group)));
      return;
    }
    const next = new Set(selectedValues);
    const allSelected = values.every((g) => next.has(g));
    if (allSelected) values.forEach((g) => next.delete(g));
    else values.forEach((g) => next.add(g));
    onChange?.(allGroups.filter((g) => next.has(g)));
  };

  const toggleOpenKey = (key) => {
    setManualOpenKeys((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const activeBook = useMemo(() => {
    if (
      (emptyMeansAll && selectedCount === 0) ||
      selectedCount === allGroups.length
    )
      return "all";
    const selected = [...selectedVisibleValues].sort();
    const match = BOOK_OPTIONS.find(([key]) => {
      const configured = (bookSettings?.[key] || [])
        .filter((group) => allGroups.includes(group))
        .sort();
      return (
        configured.length > 0 &&
        configured.length === selected.length &&
        configured.every((group, index) => group === selected[index])
      );
    });
    return match?.[0] || "manual";
  }, [
    allGroups,
    bookSettings,
    emptyMeansAll,
    selectedCount,
    selectedVisibleValues,
  ]);

  const applyBook = (book) => {
    if (book === "manual") return;
    if (book === "all") {
      onChange?.(allGroups);
      return;
    }
    const configured = new Set(bookSettings?.[book] || []);
    onChange?.(allGroups.filter((group) => configured.has(group)));
  };

  const summaryText =
    allGroups.length === 0
      ? "No groups available"
      : selectedCount === 0
        ? emptyMeansAll
          ? "All groups"
          : "No groups selected"
        : selectedCount === allGroups.length
          ? "All groups"
          : selectedCount === 1
            ? selectedVisibleValues[0]
            : `${selectedCount} groups selected`;

  return (
    <div ref={wrapperRef} className={cn("relative w-full", className)}>
      {/* Trigger button */}
      <button
        type="button"
        onClick={() => setOpen((cur) => !cur)}
        className="premium-group-trigger flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2.5 text-left text-sm transition-colors"
        style={{
          background: open ? "var(--inp-focus-bg)" : "var(--inp-bg)",
          border: open
            ? "1px solid rgb(var(--color-accent)/0.35)"
            : "1px solid var(--inp-border)",
          color: "rgb(var(--color-text))",
        }}
      >
        <div className="flex min-w-0 items-center gap-2">
          <Folder
            size={14}
            style={{ color: "rgb(var(--color-text-3))", flexShrink: 0 }}
          />
          <div className="min-w-0">
            <p
              className="text-[9px] uppercase tracking-[0.22em]"
              style={{ color: "rgb(var(--color-text-3))" }}
            >
              {label}
            </p>
            <p
              className="truncate text-xs font-medium"
              style={{ color: "rgb(var(--color-text))" }}
              title={summaryText}
            >
              {summaryText}
            </p>
          </div>
        </div>

        <div className="flex flex-shrink-0 items-center gap-1.5">
          <span className="badge text-[9px] px-1.5 py-0.5">
            {selectedCount === 0
              ? `${allGroups.length}`
              : `${selectedCount}/${allGroups.length}`}
          </span>
          <ChevronDown
            size={13}
            style={{
              color: "rgb(var(--color-text-3))",
              transform: open ? "rotate(180deg)" : "none",
              transition: "transform 0.2s",
            }}
          />
        </div>
      </button>

      {/* Dropdown */}
      {open &&
        createPortal(
          <div
            ref={dropdownRef}
            className="premium-group-dropdown fixed z-[1000] overflow-hidden rounded-2xl"
            style={{
              ...dropdownStyle,
              background: "rgb(var(--color-card))",
              border: "1px solid rgb(var(--color-border))",
              boxShadow: "0 20px 48px rgba(0,0,0,0.18)",
              backdropFilter: "blur(16px)",
            }}
          >
            {/* Toolbar */}
            <div
              className="border-b p-3"
              style={{ borderColor: "rgb(var(--color-border))" }}
            >
              {showBookPresets && (
                <label className="mb-2 block">
                  <span
                    className="mb-1 block text-[9px] font-semibold uppercase tracking-[0.2em]"
                    style={{ color: "rgb(var(--color-text-3))" }}
                  >
                    Book preset
                  </span>
                  <select
                    aria-label="Book preset"
                    value={activeBook}
                    onChange={(event) => applyBook(event.target.value)}
                    className="field-input py-1.5 text-xs"
                  >
                    <option value="all">All Groups</option>
                    {BOOK_OPTIONS.map(([key, name]) => (
                      <option
                        key={key}
                        value={key}
                        disabled={!bookSettings?.[key]?.length}
                      >
                        {name}
                        {bookSettings?.[key]?.length
                          ? ` (${bookSettings[key].length})`
                          : " (not configured)"}
                      </option>
                    ))}
                    <option value="manual">Manual selection</option>
                  </select>
                </label>
              )}
              <div className="flex items-center gap-2">
                {/* Search */}
                <div className="relative flex-1">
                  <Search
                    size={13}
                    className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2"
                    style={{ color: "rgb(var(--color-text-3))" }}
                  />
                  <input
                    ref={searchRef}
                    type="text"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search groups…"
                    className="field-input py-1.5 pl-8 pr-7 text-xs"
                  />
                  {search && (
                    <button
                      type="button"
                      onClick={() => setSearch("")}
                      className="absolute right-2 top-1/2 -translate-y-1/2"
                      style={{ color: "rgb(var(--color-text-3))" }}
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>

                <button
                  type="button"
                  onClick={() => onChange?.(allGroups)}
                  className="btn-ghost text-xs py-1.5 px-2.5"
                >
                  Select all
                </button>
                <button
                  type="button"
                  onClick={() => onChange?.([])}
                  className="btn-ghost text-xs py-1.5 px-2.5"
                >
                  Select none
                </button>
              </div>

              <div
                className="mt-2 flex items-center justify-between text-[10px]"
                style={{ color: "rgb(var(--color-text-3))" }}
              >
                <span>
                  {allGroups.length === 0
                    ? "Load a profile to fetch available groups."
                    : selectedCount === 0
                      ? emptyMeansAll
                        ? "Empty selection = all groups."
                        : "No groups selected."
                      : `${selectedCount} group${selectedCount === 1 ? "" : "s"} selected.`}
                </span>
                <span>
                  {queryActive ? "Matching only." : "Expand to browse."}
                </span>
              </div>

              {/* Selected chips */}
              {selectedCount > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {selectedVisibleValues.slice(0, 5).map((group) => (
                    <button
                      key={group}
                      type="button"
                      onClick={() =>
                        onChange?.(selectedValues.filter((i) => i !== group))
                      }
                      className="flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[10px]"
                      style={{
                        background: "var(--btn-ghost-bg)",
                        border: "1px solid var(--inp-border)",
                        color: "rgb(var(--color-text-2))",
                      }}
                      title={group}
                    >
                      <span className="max-w-[180px] truncate">{group}</span>
                      <X
                        size={10}
                        style={{
                          color: "rgb(var(--color-text-3))",
                          flexShrink: 0,
                        }}
                      />
                    </button>
                  ))}
                  {selectedVisibleValues.length > 5 && (
                    <span
                      className="rounded-full px-2 py-0.5 text-[10px]"
                      style={{
                        background: "var(--btn-ghost-bg)",
                        border: "1px solid var(--inp-border)",
                        color: "rgb(var(--color-text-3))",
                      }}
                    >
                      +{selectedVisibleValues.length - 5} more
                    </span>
                  )}
                </div>
              )}
            </div>

            {/* Tree */}
            <div className="relative max-h-[360px] overflow-y-auto p-2 custom-scrollbar space-y-1.5">
              {filteredTree.length > 0 ? (
                filteredTree.map((node) => (
                  <TreeNode
                    key={node.key}
                    node={node}
                    level={0}
                    selectedSet={selectedSet}
                    openKeys={manualOpenKeys}
                    onToggleOpen={toggleOpenKey}
                    onToggleValue={toggleGroupValues}
                    forceOpen={queryActive}
                  />
                ))
              ) : (
                <div
                  className="px-3 py-6 text-center text-xs"
                  style={{ color: "rgb(var(--color-text-3))" }}
                >
                  {allGroups.length === 0
                    ? "No groups available for this profile."
                    : "No groups match your search."}
                </div>
              )}
            </div>
            <div
              className="flex items-center justify-between border-t p-3"
              style={{ borderColor: "rgb(var(--color-border))" }}
            >
              <span
                className="text-[10px]"
                style={{ color: "rgb(var(--color-text-3))" }}
              >
                {selectedCount} of {allGroups.length} selected
              </span>
              <button
                type="button"
                className="btn-secondary px-4 py-1.5 text-xs"
                onClick={() => setOpen(false)}
              >
                Done
              </button>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
