#!/usr/bin/env python

# Jeremy Gordon

# Basic implementation of HTM network (Neumenta, Hawkins)
# Adjusted to support continuous activations that fall off over time at different rates
# Temporal pooler removed in favor of spatial-temperal pooling
# http://numenta.org/resources/HTM_CorticalLearningAlgorithms.pdf


# CURRENT PROBLEMS
# -----------------------
# Regional focus (upper left)
# Boosting takes over (works better without)
# Do patterns reinforce themselves too much? Lead to constant
# pattern as distal bias continues to predict same? Or 
# will these patterns die randomly as proximal goes away?
# Distal biases either saturate or go to 0 after 100 iterations, why?
# Looking for invariant representations in the bias layer (pattern that predicts 
# itself)
# Are we learning before render, causing confusing synapse audits?
# We can't have all cells in SDR learn the same way or we wont produce
# unique pattern detectors that can detect multiple longer-term pattersn
# Pick learners randomly?

# >> TOdo my efficiency changes in effiency branch lost value. What changes?
# -----------------------

import numpy as np
import random
import math
import util

# Settings

VERBOSITY = 1
PROXIMAL_ACTIVATION_THRESHHOLD = 2 # Activation threshold for a segment. If the number of active connected synapses in a segment is greater than activationThreshold, the segment is said to be active. 
DISTAL_ACTIVATION_THRESHOLD = 4
DEF_MIN_OVERLAP = 2
CONNECTED_PERM = 0.2  # If the permanence value for a synapse is greater than this value, it is said to be connected.
DUTY_HISTORY = 100
BOOST_MULTIPLIER = 2
OVERLAP_ACTIVATION_THRESHHOLD = 3
INHIBITION_RADIUS_DISCOUNT = 0.6
INIT_PERMANENCE = 0.2  # New learned synapses
INIT_PERMANENCE_JITTER = 0.05  # Max offset from CONNECTED_PERM when initializing synapses
SYNAPSE_ACTIVATION_LEARN_THRESHHOLD = 0.3
INIT_PERMANENCE_LEARN_INC_CHANGE = 0.02
INIT_PERMANENCE_LEARN_DEC_CHANGE = 0.005
DESIRED_LOCAL_ACTIVITY = 2
DO_BOOSTING = True
CHANCE_OF_INHIBITORY = 0.5
DISTAL_BIAS_EFFECT = 0.3 # Account for high bias
OVERLAP_EFFECT = 0.7
T_START_BOOSTING = 50
DISTAL_SYNAPSE_CHANCE = 0.4

def log(message, level=1):
    if VERBOSITY >= level:
        print message

def printarray(array, coerce_to_int=True, continuous=False):
    if continuous:
        # Takes an array of doubles
        out = ""
        _max = max(array)
        if _max:
            normalized = [x/_max for x in array]
        else:
            normalized = array
        for item in normalized:
            if math.isnan(item):
                simplified = "?"
            else:
                if item < 0:
                    simplified = "N" # Negative
                else:
                    simplified = str(int(item*5))
                    if simplified == "0":
                        simplified = "."
            out += simplified
        out += " (max: %.1f)" % _max
        return out
    else:
        if type(array[0]) is int or coerce_to_int:
            return ''.join([str(int(x)) for x in array])
        elif type(array[0]) in [float, np.float64]:
            return '|'.join([str(int(x)) for x in array])


class Segment(object):
    '''
    Dendrite segment of cell (proximal or distal)
    Store all synapses activations / connectedness in arrays
    '''
    PROXIMAL = 1
    DISTAL = 2

    def __init__(self, cell, index, region, type=None):
        self.index = index
        self.region = region
        self.cell = cell
        self.type = type if type else self.PROXIMAL

        # Synapses
        self.syn_sources = [] # Index of source (either input or cell in region)
        self.syn_excitatory = [] # 0 or 1 
        self.syn_permanences = [] # (0,1)


    def __repr__(self):
        t = self.region.brain.t
        return "<Segment type=%s index=%d potential=%d connected=%d>" % (self.print_type(), self.index, self.n_synapses(), len(self.connected_synapses()))

    def initialize(self):
        if self.type == self.PROXIMAL:
            # Setup initial potential synapses for proximal segments
            MAX_INIT_SYNAPSE_CHANCE = 0.5
            MIN_INIT_SYNAPSE_CHANCE = 0.05
            n_inputs = self.region.n_inputs
            cell_x, cell_y = util.coords_from_index(self.cell.index, self.region._cell_side_len())
            for source in range(n_inputs):
                # Loop through all inputs and randomly choose to create synapse or not
                input_x, input_y = util.coords_from_index(source, self.region._input_side_len())
                dist = util.distance((cell_x, cell_y), (input_x, input_y))
                max_distance = self.region.diagonal
                chance_of_synapse = ((MAX_INIT_SYNAPSE_CHANCE - MIN_INIT_SYNAPSE_CHANCE) * (1 - float(dist)/max_distance)) + MIN_INIT_SYNAPSE_CHANCE
                add_synapse = random.random() < chance_of_synapse
                if add_synapse:
                    self.add_synapse(source)                
        else:
            # Distal
            for index in range(self.region.n_cells):
                chance_of_synapse = DISTAL_SYNAPSE_CHANCE
                add_synapse = random.random() < chance_of_synapse
                if add_synapse:
                    self.add_synapse(index, permanence=0.1)
        log_message = "Initialized %s" % self
        log(log_message)

    def proximal(self):
        return self.type == self.PROXIMAL

    def add_synapse(self, source_index=0, permanence=None):
        excitatory = random.random() > CHANCE_OF_INHIBITORY
        if permanence is None:
            permanence = CONNECTED_PERM + INIT_PERMANENCE_JITTER*(random.random()-0.5)
        self.syn_sources.append(source_index)
        self.syn_permanences.append(permanence)
        self.syn_excitatory.append(excitatory)

    def remove_synapse(self, index):
        pass

    def distance_from(self, coords_xy, index=0):
        source_xy = util.coords_from_index(self.syn_sources[index], self.region._input_side_len())
        return util.distance(source_xy, coords_xy)

    def connected(self, index=0, connectionPermanence=CONNECTED_PERM):
        return self.syn_permanences[index] > connectionPermanence

    def print_type(self):
        return "Proximal" if self.type == self.PROXIMAL else "Distal"

    def contribution(self, index=0, absolute=False):
        '''
        Returns a contribution (activation) [-1.0,1.0] of synapse at index
        '''
        if self.proximal():
            activation = self.region._input_active(self.syn_sources[index])
        else:
            activation = self.region.cells[index].activation
        mult = 1 if self.syn_excitatory[index] else -1
        if not absolute:
            activation *= mult
        return activation

    def total_activation(self):
        '''
        Returns sum of contributions from all connected synapses
        Return: double (not bounded)
        '''
        return sum([self.contribution(i) for i in self.connected_synapses()])

    def active(self): 
        '''
        '''
        threshold = PROXIMAL_ACTIVATION_THRESHHOLD if self.proximal() else DISTAL_ACTIVATION_THRESHOLD
        return self.total_activation() > threshold

    def n_synapses(self):
        return len(self.syn_sources)

    def source(self, index):
        return self.syn_sources[index]

    def connected_synapses(self, connectionPermanence=CONNECTED_PERM):
        '''
        Return array of indexes
        '''
        connected_indexes = [i for i,p in enumerate(self.syn_permanences) if p > connectionPermanence]
        return connected_indexes


class Cell(object):
    '''
    An HTM abstraction of one or more biological neurons
    Has multiple dendrite segments connected to inputs
    '''

    def __init__(self, region, index, n_proximal_segments=2, n_distal_segments=5):
        self.index = index
        self.region = region
        self.n_proximal_segments = n_proximal_segments
        self.n_distal_segments = n_distal_segments
        self.distal_segments = []
        self.proximal_segments = []
        self.activation = 0.0 # [0.0, 1.0]
        self.coords = util.coords_from_index(index, self.region._cell_side_len())
        self.fade_rate = random.uniform(0.2, 0.5)

        # History
        self.recent_active_duty = []  # After inhibition, list of bool
        self.recent_overlap_duty = []  # Overlap > min_overlap, list of bool

    def __repr__(self):
        return "<Cell index=%d activation=%.1f />" % (self.index, self.activation)

    def initialize(self):
        for i in range(self.n_proximal_segments):
            proximal = Segment(self, i, self.region, type=Segment.PROXIMAL)
            proximal.initialize() # Creates synapses
            self.proximal_segments.append(proximal)
        for i in range(self.n_distal_segments):
            distal = Segment(self, i, self.region, type=Segment.DISTAL)
            distal.initialize() # Creates synapses
            self.distal_segments.append(distal)
        log("Initialized %s" % self)

    def active_segments(self, type=Segment.PROXIMAL):
        segs = self.proximal_segments if type == Segment.PROXIMAL else self.distal_segments
        return filter(lambda seg : seg.active(), segs)

    def update_duty_cycles(self, active=False, overlap=False):
        '''
        Add current active state & overlap state to history and recalculate duty cycles
        '''
        self.recent_active_duty.insert(0, 1 if active else 0)
        self.recent_overlap_duty.insert(0, 1 if overlap else 0)
        if len(self.recent_active_duty) > DUTY_HISTORY:
            # Truncate
            self.recent_active_duty = self.recent_active_duty[:DUTY_HISTORY]  
        if len(self.recent_overlap_duty) > DUTY_HISTORY:
            # Truncate
            self.recent_overlap_duty = self.recent_overlap_duty[:DUTY_HISTORY]  
        cell_active_duty = sum(self.recent_active_duty) / float(len(self.recent_active_duty))
        cell_overlap_duty = sum(self.recent_overlap_duty) / float(len(self.recent_overlap_duty))
        self.region.active_duty_cycle[self.index] = cell_active_duty
        self.region.overlap_duty_cycle[self.index] = cell_overlap_duty

    def connected_receptive_field_size(self):
        '''
        Returns max distance (radius) among currently connected proximal synapses
        '''
        connected_indexes = self.connected_synapses(type=Segment.PROXIMAL)
        side_len = self.region._input_side_len()
        distances = []
        for i in connected_indexes:
            distance = util.distance(util.coords_from_index(i, side_len), self.coords)
            distances.append(distance)
        if distances:
            return max(distances)
        else:
            return 0

    def connected_synapses(self, type=Segment.PROXIMAL):
        synapses = []
        segs = self.proximal_segments if type == Segment.PROXIMAL else self.distal_segments
        for seg in segs:
            synapses.extend(seg.connected_synapses())
        return synapses


class Region(object):
    '''
    Made up of many columns
    '''

    def __init__(self, brain, index, permanence_inc=INIT_PERMANENCE_LEARN_INC_CHANGE, permanence_dec=INIT_PERMANENCE_LEARN_DEC_CHANGE, n_cells=10, n_inputs=20):
        self.index = index
        self.brain = brain

        # Region constants (spatial)
        self.permanence_inc = permanence_inc
        self.permanence_dec = permanence_dec
        self.inhibition_radius = 0 # Average connected receptive field size of the columns

        # Hierarchichal setup
        self.n_cells = n_cells
        self.n_inputs = n_inputs
        self.cells = []

        # State (historical)
        self.input = None  # Inputs at time t - input[t, j] is double [0.0, 1.0]

        # State
        self.overlap = np.zeros(self.n_cells)  # Overlap for each cell. overlap[c] is double
        self.boost = np.ones(self.n_cells, dtype=float)  # Boost value for cell c
        self.bias = np.zeros(self.n_cells)
        self.active_duty_cycle = np.zeros((self.n_cells))  # Sliding average: how often column c has been active after inhibition (e.g. over the last 1000 iterations).
        self.overlap_duty_cycle = np.zeros((self.n_cells))  # Sliding average: how often column c has had significant overlap (> min_overlap)

        # Helper constants
        self.diagonal = 1.414*2*math.sqrt(n_cells)

        
    def __str__(self):
        return "<Region inputs=%d cells=%d />" % (self.n_inputs, len(self.cells))

    def print_cells(self):
        activations = [cell.activation for cell in self.cells]
        return printarray(activations, continuous=True)

    def initialize(self):
        # Create cells
        for i in range(self.n_cells):
            c = Cell(region=self, index=i)
            c.initialize()
            self.cells.append(c)
        print "Initialized %s" % self
        
    def _input_side_len(self):
        return math.sqrt(self.n_inputs)

    def _cell_side_len(self):
        return math.sqrt(self.n_cells)

    def _get_active_cells(self):
        pass

    def _input_active(self, j):
        '''
        At index j, at time t - tMinus
        '''
        return self.input[j]

    def _kth_score(self, cells, k):
        '''
        Given list of cells, calculate kth highest overlap value
        '''
        if cells:
            overlaps = []
            for c in cells:
                overlaps.append(self.overlap[c.index])
            _k = min([k, len(overlaps)]) # TODO: Ok to pick last if k > overlaps?
            overlaps = sorted(overlaps, reverse=True) # Highest to lowest
            kth_overlap = overlaps[_k-1]            
            # log("%s is %dth highest overlap score in sequence: %s" % (kth_overlap, k, overlaps))
            return kth_overlap
        return 0 # Shouldn't happen?

    def _max_duty_cycle(self, cells):
        if cells:
            return max([self.active_duty_cycle[c.index] for c in cells])
        else:
            return 0

    def _neighbors_of(self, cell):
        '''
        Return all cells within inhibition radius
        '''
        _neighbors = []
        for c in self.cells:
            if cell.index == c.index:
                continue
            dist = util.dist_from_indexes(c.index, cell.index, self._cell_side_len())
            if dist <= self.inhibition_radius:
                _neighbors.append(c)
        # log("Got %d neighbors. Overlaps: %s" % (len(_neighbors), [self.overlap[n.index] for n in _neighbors]))
        return _neighbors

    def _boost_function(self, c, min_duty_cycle):
        if self.active_duty_cycle[c] >= min_duty_cycle:
            b = 1.0
        else:
            b = 1 + (min_duty_cycle - self.active_duty_cycle[c]) * BOOST_MULTIPLIER
        return b

    def _increase_permanences(self, c, increase):
        '''
        Increase the permanence value of every synapse (cell c) by a increase factor
        TODO: Should this be for a specific segment?
        '''
        cell = self.cells[c]
        for seg in self.cells[c].proximal_segments:
            seg.syn_permanences = [min([x+increase, 1.0]) for x in seg.syn_permanences]
        # for seg in self.cells[c].distal_segments:
        #     seg.syn_permanences = [min([x+increase, 1.0]) for x in seg.syn_permanences]

    def calculate_distal_biases(self):
        '''
        For each cell calculate aggregate activations from distal segments
        Result is a bias array that will be used during overlap to increase
        chances we activate 'predicted' cells
        '''
        bias = np.zeros(len(self.cells))  # Initialize bias to 0
        for i, c in enumerate(self.cells):
            for seg in c.distal_segments:
                if seg.active():
                    bias[i] += 1
        return bias

    def do_overlap(self):
        '''
        Return overlap as a double for each cell representing boosted, biased
        activation from inputs
        '''
        overlaps = np.zeros(len(self.cells))  # Initialize overlaps to 0
        for i, c in enumerate(self.cells):
            for seg in c.proximal_segments:
                if seg.active():
                    overlaps[i] += 1
            overlaps[i] *= OVERLAP_EFFECT
            # Handle bias
            if DISTAL_BIAS_EFFECT:
                overlaps[i] += DISTAL_BIAS_EFFECT * self.bias[c.index]
            overlaps[i] *= self.boost[i]
        return overlaps

    def do_inhibition(self):
        '''
        Get active cells after inhibition around strongly overlapped cells
        '''
        active = np.zeros(len(self.cells))
        for c in self.cells:
            ovlp = self.overlap[c.index]
            neighbors = self._neighbors_of(c)
            kth_ovlerap = self._kth_score(neighbors, k=DESIRED_LOCAL_ACTIVITY)
            if ovlp > 0 and ovlp >= kth_ovlerap:
                active[c.index] = True
            # log("Activate attempts: %s, my overlap: %s, sum of neighbor overlap: %s, active: %s" % (activate_attempts, ovlp, sum_of_neighbor_overlap, active[c.index]))
            # kth_highest_overlap = self._kth_score(self._neighbors_of(c), self.desired_local_activity)
            # minLocalActivity = kth_highest_overlap
            # ovlp = self.overlap[c.index]
            # if minLocalActivity > 0 and ovlp >= minLocalActivity:
            #     # log("Activating %d because overlap %s > %s" % (c.index, ovlp, minLocalActivity))
            #     active[c.index] = 1
        return active

    def do_learning(self, activating):
        '''
        Update permanences
        On activating cells, increase permenences for each excitatory synapse above a min. contribution
        On non-activating cells, increase permenences for each inhibitory synapse above a min. contribution
        '''
        n_increased_prox = n_decreased_prox = n_increased_dist = n_decreased_dist = n_changed_prox = n_changed_dist = 0
        for i, is_activating in enumerate(activating):
            cell = self.cells[i]
            change_permanences = is_activating
            # Proximal 
            if change_permanences:
                for seg in cell.proximal_segments:
                    for i in range(seg.n_synapses()):
                        was_connected = seg.connected(i)
                        increase_permanence = seg.contribution(i, absolute=True) >= SYNAPSE_ACTIVATION_LEARN_THRESHHOLD and ((is_activating and seg.syn_excitatory[i]) or (not is_activating and not seg.syn_excitatory[i]))
                        if increase_permanence:
                            n_increased_prox += 1
                            seg.syn_permanences[i] += self.permanence_inc
                            seg.syn_permanences[i] = min(1.0, seg.syn_permanences[i])
                        else:
                            n_decreased_prox +=1 
                            seg.syn_permanences[i] -= self.permanence_dec
                            seg.syn_permanences[i] = max(0.0, seg.syn_permanences[i])
                        connection_changed = was_connected != seg.connected(i)
                        if connection_changed:
                            n_changed_prox += 1

            # Distal
            if change_permanences: # Only learn on activating cells
                for seg in cell.distal_segments:
                    for i in range(seg.n_synapses()):
                        was_connected = seg.connected(i)
                        increase_permanence = seg.contribution(i, absolute=True) >= SYNAPSE_ACTIVATION_LEARN_THRESHHOLD and ((is_activating and seg.syn_excitatory[i]) or (not is_activating and not seg.syn_excitatory[i]))
                        if increase_permanence and seg.syn_permanences[i] < 1.0:
                            n_increased_dist += 1
                            seg.syn_permanences[i] += self.permanence_inc
                            seg.syn_permanences[i] = min(1.0, seg.syn_permanences[i])
                        elif not increase_permanence and seg.syn_permanences[i] > 0.0:
                            n_decreased_dist +=1 
                            seg.syn_permanences[i] -= self.permanence_dec
                            seg.syn_permanences[i] = max(0.0, seg.syn_permanences[i])
                        connection_changed = was_connected != seg.connected(i)
                        if connection_changed:
                            n_changed_dist += 1

        log("Distal: +%d/-%d (%d changed), Proximal: +%d/-%d (%d changed)" % (n_increased_dist, n_decreased_dist, n_changed_dist, n_increased_prox, n_increased_dist, n_changed_prox))

        n_boosted = 0
        all_field_sizes = []
        for i, cell in enumerate(self.cells):
            neighbors = self._neighbors_of(cell)
            min_duty_cycle = 0.01 * self._max_duty_cycle(neighbors) # Based on active duty
            cell_active = activating[i]
            sufficient_overlap = self.overlap[i] > self.brain.min_overlap
            cell.update_duty_cycles(active=cell_active, overlap=sufficient_overlap)
            if DO_BOOSTING and self.brain.t > T_START_BOOSTING:
                self.boost[i] = self._boost_function(i, min_duty_cycle)  # Updates boost value for cell (higher if below min)

                # Check if overlap duty cycle less than minimum (note: min is calculated from max *active* not overlap)
                if self.overlap_duty_cycle[i] < min_duty_cycle:
                    # log("Increasing permanences for cell %s in region %d due to overlap duty cycle below min: %s" % (i, self.index, min_duty_cycle))
                    self._increase_permanences(i, 0.1 * CONNECTED_PERM)
                    n_boosted += 1

            all_field_sizes.append(self.cells[i].connected_receptive_field_size())
    
        if n_boosted:
            log("Boosting %d due to low overlap duty cycle" % n_boosted)

        # Update inhibition radius (based on updated active connections in each column)
        self.inhibition_radius = util.average(all_field_sizes) * INHIBITION_RADIUS_DISCOUNT
        min_positive_radius = 1.0
        if self.inhibition_radius and self.inhibition_radius < min_positive_radius:
            self.inhibition_radius = min_positive_radius
        # log("Setting inhibition radius to %s" % self.inhibition_radius)

    def tempero_spatial_pooling(self, learning_enabled=True):
        '''
        Temporal-Spatial pooling routine
        --------------
        Takes input and calculates active columns (sparse representation) for input into temporal pooling
        '''

        # Phase 1: Calculate Distal Biases (TM?)
        self.bias = self.calculate_distal_biases()
        log("%s << Bias" % printarray(self.bias, continuous=True), level=2)

        # Phase 2: Overlap
        self.overlap = self.do_overlap()
        log("%s << Overlap (normalized)" % printarray(self.overlap, continuous=True), level=2)

        # Phase 3: Inhibition    
        activating = self.do_inhibition()
        log("%s << Activating (inhibited)" % printarray(activating, continuous=True), level=2)

        # Update activations
        for i, cell in enumerate(self.cells):
            if activating[i]:
                cell.activation = 1.0  # Max out
            else:
                cell.activation -= cell.fade_rate
            if cell.activation < 0:
                cell.activation = 0.0

        log("%s << Activations" % self.print_cells(), level=2)

        if VERBOSITY >= 1:
            # Log average synapse permanence in region
            permanences = []
            n_connected = 0
            n_synapses = 0
            for cell in self.cells:
                for seg in cell.distal_segments:
                    permanences.append(util.average(seg.syn_permanences))
                    n_synapses += seg.n_synapses()
                    n_connected += len(filter(lambda x : x > CONNECTED_PERM, seg.syn_permanences))
            ave_permanence = util.average(permanences)
            log("R%d - average distal synapse permanence: %.1f (%.1f%% connected of %d)" % (self.index, ave_permanence, (n_connected/float(n_synapses))*100., n_synapses), level=1)

        # Phase 3: Learning
        if learning_enabled:
            log("%s << Active Duty Cycle" % printarray(self.active_duty_cycle, continuous=True), level=2)
            log("%s << Overlap Duty Cycle" % printarray(self.overlap_duty_cycle, continuous=True), level=2)            
            self.do_learning(activating)


    ##################
    # Primary Step Function
    ##################

    def step(self, input, learning_enabled=False):
        self.input = input
        
        self.tempero_spatial_pooling(learning_enabled=learning_enabled)  # Calculates active cells
        
        return [c.activation for c in self.cells]



class CHTMBrain(object):

    def __init__(self, cells_per_region=None, min_overlap=DEF_MIN_OVERLAP, r1_inputs=1):
        self.regions = []
        self.t = 0
        self.active_behaviors = []
        self.cells_per_region = cells_per_region
        self.n_inputs = r1_inputs
        self.min_overlap = min_overlap # A minimum number of inputs that must be active for a column to be considered during the inhibition step

    def __repr__(self):
        return "<HTMBrain regions=%d>" % len(self.regions)

    def initialize(self):
        n_inputs = self.n_inputs
        for i, cpr in enumerate(self.cells_per_region):
            r = Region(self, i, n_inputs=n_inputs, n_cells=cpr)
            r.initialize()
            n_inputs = cpr  # Next region will have 1 input for each output cell
            self.regions.append(r)
        print "Initialized %s" % self

    def process(self, readings, learning=False):
        '''
        Step through all regions inputting output of each into next
        Returns output of last region
        '''
        print "~~~~~~~~~~~~~~~~~ Processing inputs at T%d" % self.t
        _in = readings
        for i, r in enumerate(self.regions):
            log("Step processing for region %d\n%s << Input" % (i, printarray(_in, continuous=True)), level=2)
            out = r.step(_in, learning_enabled=learning)
            _in = out
        self.t += 1 # Move time forward one step
        return out
