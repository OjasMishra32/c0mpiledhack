import type { Action, Event, ObservedObject, Worker, Zone } from '../types/hive';

const now = new Date().toISOString();

export const dummyWorkers: Worker[] = [
  {
    id: 'worker_a', display_name: 'Worker A', callsign: 'ALPHA', color: '#5AC8FA',
    status: 'executing', connected: true, available: true, current_action_id: 'a1',
    reachable_zones: ['zone_1', 'zone_2', 'field'], role: 'Picker A',
    supported_actions: ['pick_up', 'move_to_zone', 'place_in_zone'],
    position: { x: 0.1, y: 0.15 }, last_seen_at: now, assignment_count: 3, confidence: 0.95,
  },
  {
    id: 'worker_b', display_name: 'Worker B', callsign: 'BRAVO', color: '#5E5CE6',
    status: 'ready', connected: true, available: true, current_action_id: null,
    reachable_zones: ['zone_2', 'zone_3', 'field'], role: 'Picker B',
    supported_actions: ['pick_up', 'move_to_zone', 'hold'],
    position: { x: 0.1, y: 0.35 }, last_seen_at: now, assignment_count: 1, confidence: 1.0,
  },
  {
    id: 'worker_c', display_name: 'Worker C', callsign: 'CHARLIE', color: '#30D158',
    status: 'executing', connected: true, available: true, current_action_id: 'a3',
    reachable_zones: ['zone_1', 'zone_4', 'field'], role: 'Packer',
    supported_actions: ['place_in_zone', 'place_on', 'inspect'],
    position: { x: 0.1, y: 0.55 }, last_seen_at: now, assignment_count: 4, confidence: 0.88,
  },
  {
    id: 'worker_d', display_name: 'Worker D', callsign: 'DELTA', color: '#FF9F0A',
    status: 'blocked', connected: true, available: true, current_action_id: 'a5',
    reachable_zones: ['zone_3', 'zone_4'], role: 'Restocker',
    supported_actions: ['move_to_zone', 'place_in_zone'],
    position: { x: 0.1, y: 0.75 }, last_seen_at: now, assignment_count: 2, confidence: 1.0,
  },
  {
    id: 'worker_e', display_name: 'Worker E', callsign: 'ECHO', color: '#FF375F',
    status: 'disconnected', connected: false, available: false, current_action_id: null,
    reachable_zones: [], role: 'Floater',
    supported_actions: ['pick_up', 'move_to_zone', 'inspect'],
    position: { x: 0.1, y: 0.95 }, last_seen_at: now, assignment_count: 0, confidence: 1.0,
  },
];

export const dummyObjects: ObservedObject[] = [
  {
    id: 'obj_1',
    descriptor: { dominant_hsv: [8, 214, 190], color_name: 'red', color_hex: '#C43A2E', area_norm: 0.021, aspect: 1.08, circularity: 0.86, shape_hint: 'round' },
    position: { x: 0.24, y: 0.61 }, zone: 'zone_1', visible: true, confidence: 0.91,
    first_seen_at: now, last_updated_at: now, source: 'vision',
    semantic_label: 'red plastic cup', role: 'the priority item', role_confidence: 0.88,
    held_by: null, stacked_on: null, locked_by: null,
  },
  {
    id: 'obj_2',
    descriptor: { dominant_hsv: [212, 180, 160], color_name: 'blue', color_hex: '#3A6FC4', area_norm: 0.03, aspect: 0.95, circularity: 0.4, shape_hint: 'rectangular' },
    position: { x: 0.4, y: 0.5 }, zone: 'zone_3', visible: true, confidence: 0.88,
    first_seen_at: now, last_updated_at: now, source: 'vision',
    semantic_label: 'blue folder', role: null, role_confidence: null,
    held_by: null, stacked_on: null, locked_by: null,
  },
  {
    id: 'obj_3',
    descriptor: { dominant_hsv: [48, 200, 210], color_name: 'yellow', color_hex: '#E0B227', area_norm: 0.026, aspect: 1.5, circularity: 0.3, shape_hint: 'rectangular' },
    position: { x: 0.6, y: 0.4 }, zone: 'zone_2', visible: true, confidence: 0.84,
    first_seen_at: now, last_updated_at: now, source: 'vision',
    semantic_label: 'yellow handheld scanner', role: 'the scanner', role_confidence: 0.92,
    held_by: null, stacked_on: null, locked_by: 'a3',
  },
];

export const dummyZones: Zone[] = [
  { id: 'zone_1', label: 'Inbound Dock', bounds: { x: 0.05, y: 0.05, w: 0.2, h: 0.4 }, occupancy: ['obj_1'], status: 'active', source: 'detected' },
  { id: 'zone_2', label: 'Pack Station', bounds: { x: 0.74, y: 0.28, w: 0.24, h: 0.44 }, occupancy: ['obj_3'], status: 'active', source: 'detected' },
  { id: 'zone_3', label: 'Pick Aisle A', bounds: { x: 0.3, y: 0.3, w: 0.2, h: 0.4 }, occupancy: ['obj_2'], status: 'satisfied', source: 'detected' },
  { id: 'zone_4', label: 'Pick Aisle B', bounds: { x: 0.5, y: 0.55, w: 0.2, h: 0.4 }, occupancy: [], status: 'pending', source: 'detected' },
];

export const dummyActions: Action[] = [
  {
    id: 'a1', type: 'pick_up', description: 'Pick up the priority item.', object_id: 'obj_1',
    target_object_id: null, target_zone: null, assigned_worker_id: 'worker_a',
    assignment_reason: 'ALPHA selected: closest to the item, currently idle.',
    dependencies: [], status: 'executing', priority: 90, timeout_seconds: 25,
    expected_predicates: [], instruction: null, retry_count: 0, max_retries: 2,
    is_recovery: false, created_at: now, dispatched_at: now, completed_at: null,
    lock_targets: ['object:obj_1'],
  },
  {
    id: 'a2', type: 'move_to_zone', description: 'Move the blue folder to Pack Station.',
    object_id: 'obj_2', target_object_id: null, target_zone: 'zone_2', assigned_worker_id: 'worker_b',
    assignment_reason: 'BRAVO selected: reachable, no conflicting activity.',
    dependencies: [], status: 'available', priority: 80, timeout_seconds: 25,
    expected_predicates: [], instruction: null, retry_count: 0, max_retries: 2,
    is_recovery: false, created_at: now, dispatched_at: null, completed_at: null,
    lock_targets: ['object:obj_2'],
  },
  {
    id: 'a3', type: 'place_in_zone', description: 'Place the handheld scanner in Pack Station.',
    object_id: 'obj_3', target_object_id: null, target_zone: 'zone_2', assigned_worker_id: 'worker_c',
    assignment_reason: 'CHARLIE selected: closest to the scanner, currently idle.',
    dependencies: [], status: 'executing', priority: 85, timeout_seconds: 25,
    expected_predicates: [], instruction: null, retry_count: 0, max_retries: 2,
    is_recovery: false, created_at: now, dispatched_at: now, completed_at: null,
    lock_targets: ['object:obj_3', 'zone:zone_2'],
  },
  {
    id: 'a4', type: 'place_in_zone', description: 'Place the priority item in Pack Station.',
    object_id: 'obj_1', target_object_id: null, target_zone: 'zone_2', assigned_worker_id: null,
    assignment_reason: null, dependencies: ['a1', 'a3'], status: 'queued', priority: 70,
    timeout_seconds: 25, expected_predicates: [], instruction: null, retry_count: 0,
    max_retries: 2, is_recovery: false, created_at: now, dispatched_at: null, completed_at: null,
    lock_targets: ['zone:zone_2'],
  },
  {
    id: 'a5', type: 'move_to_zone', description: 'Restock Pick Aisle B.', object_id: null,
    target_object_id: null, target_zone: 'zone_4', assigned_worker_id: 'worker_d',
    assignment_reason: 'DELTA selected: dedicated restocker.', dependencies: [],
    status: 'blocked', priority: 60, timeout_seconds: 25, expected_predicates: [],
    instruction: null, retry_count: 0, max_retries: 2, is_recovery: false, created_at: now,
    dispatched_at: now, completed_at: null, lock_targets: ['zone:zone_4'],
  },
];

export const dummyEvents: Event[] = [
  { id: 'evt_5', seq: 5, timestamp: now, type: 'action_dispatched', severity: 'info', actor: 'hive', message: 'CHARLIE dispatched: move handheld scanner to Pack Station', metadata: {} },
  { id: 'evt_4', seq: 4, timestamp: now, type: 'action_verified', severity: 'success', actor: 'hive', message: 'Water supply confirmed at Medical Station · 84%', metadata: {} },
  { id: 'evt_3', seq: 3, timestamp: now, type: 'replanning', severity: 'warn', actor: 'hive', message: 'Replanning around blocked packing workflow.', metadata: {} },
  { id: 'evt_2', seq: 2, timestamp: now, type: 'deviation_detected', severity: 'critical', actor: 'vision', message: 'Handheld scanner detected outside planned route.', metadata: {} },
  { id: 'evt_1', seq: 1, timestamp: now, type: 'action_dispatched', severity: 'info', actor: 'hive', message: 'ALPHA dispatched: pick up the priority item', metadata: {} },
];
