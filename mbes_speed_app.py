import math
import streamlit as st

REFERENCES_HELP = """
**Multibeam vessel-speed and survey-planning helper**

**Main formulas / standards used:**
• IHO S-44 Edition 6.1.0 (Table 1: TVU; Annex-C: QC guidance & line spacing)
• IHO C-13 *Manual on Hydrography* (Ch. 3–4: feature detection, overlap)
• NOAA HSSD (Hydrographic Survey Specifications & Deliverables)
• National CHS-style specifications for node density, overlap & cube detection
"""



# ---------------- SONAR DEAD-TIME PRESETS ----------------
SONAR_DEAD_TIMES = {
    "R2Sonic (High Rate)": 0.02,            # ~10–30 ms
    "Norbit WBMS (High Rate)": 0.03,
    "Teledyne Reson T20/T50 (Shallow)": 0.04,
    "Kongsberg EM2040 (Shallow)": 0.05,
    "Teledyne/Simrad Shelf MBES": 0.10,
    "Kongsberg Deepwater EM302/304": 0.15,
    "Atlas Hydrosweep (Deepwater)": 0.20,
    "Generic Shallow MBES": 0.05,
    "Generic Deep MBES": 0.15,
    "Custom / Other Sonar": None,          # no preset
}


# -----------------------------------------------------------------------------
# IHO ORDER PRESETS
# -----------------------------------------------------------------------------
# Coverage presets are S-44 style "seafloor coverage" (e.g. 200% = 50% overlap)
ORDER_COVERAGE = {
    # Bathymetric / seafloor coverage in “S-44 style” terms
    # 100 % = full coverage with no required overlap
    # 200 % = 50 % overlap (each point seen twice)
    "Exclusive Order": 200.0,    # very conservative default
    "Special Order": 100.0,      # typical 100 %
    "Order 1a": 100.0,
    "Order 1b": 100.0,
    "Order 2": 100.0,

    "Custom (set coverage manually)": None,
}

# Ratio used to convert optimum speed to an operational minimum
MIN_SPEED_RATIO = 0.5  # 0.5 ⇒ min speed is 50% of optimum


# a, b coefficients for TVU (Total Vertical Uncertainty) from S-44 Table 1
# TVU_max(d) = sqrt( a^2 + (b * d)^2 )
ORDER_TVU_AB = {
    "Special Order": (0.25, 0.0075),   # a, b
    "Order 1a": (0.5, 0.013),
    "Order 1b": (0.5, 0.013),
    "Order 2": (1.0, 0.023),
    "Exclusive Order": (0.15, 0.0075),
}

# S-44 Edition 6.1.0 feature-detection thresholds (cubic features).
# Special: cubes > 1 m
# Order 1a: cubes > 2 m (d <= 40 m) or > 10 % of depth for d > 40 m
# Exclusive: cubes > 0.5 m
# Orders 1b and 2: no explicit cubic-feature requirement.
def s44_cubic_feature_requirement(order_name: str, depth_m: float) -> float | None:
    """
    Return the S-44 Ed. 6.1.0 'system detection capability' threshold for
    cubic features (edge length in metres) for a given survey order and depth.

    These edge lengths are MINIMUM requirements; national specs often tighten
    them for hazardous or rocky bottoms (see IHO C-13 and national standards).
    """
    if order_name == "Exclusive Order":
        return 0.5
    if order_name == "Special Order":
        return 1.0
    if order_name == "Order 1a":
        if depth_m <= 40.0:
            return 2.0
        # deeper than 40 m: 10 % of depth
        return 0.10 * depth_m
    # Orders 1b and 2: not specified in S-44
    return None


ORDERS = [
    "Special Order",
    "Order 1a",
    "Order 1b",
    "Order 2",
    "Exclusive Order",
    "Custom (set coverage manually)",
]

# Bottom-type factors: tighten the cube size for rough / rocky ground.
# S-44 explicitly says the 0.5 / 1 / 2 m cubes are MINIMUM requirements; in
# hazardous / rocky bottoms authorities often require smaller features to be
# detectable.
BOTTOM_TYPE_FACTORS = {
    "Baseline S-44 (smooth / low obstruction risk)": 1.0,
    "Rough / rocky / obstruction-prone (tighten by ×0.5)": 0.5,
}


KNOTS_PER_M_S = 1.94384449244
KM_PER_NM = 1.852  # km in a nautical mile


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------
def ping_interval(
    depth_m: float,
    sound_speed_ms: float,
    dead_time_s: float,
    swath_deg: float,
) -> float:
    """
    Two-way travel time to the OUTER BEAM + user dead time.

    Matches the Tkinter GUI:
        θ = swath / 2
        R = D / cos(θ)
        t_2way = 2R / c
        T = t_2way + Δt
    """
    theta_rad = math.radians(swath_deg / 2.0)
    R = depth_m / math.cos(theta_rad)
    t_2way = 2.0 * R / sound_speed_ms
    return t_2way + dead_time_s



def along_track_footprint(depth_m: float, tx_beam_width_deg: float) -> float:
    """
    Very simple along-track footprint length [m].
    L ≈ 2 * D * tan(phi_T / 2)
    """
    half_bw_rad = math.radians(tx_beam_width_deg / 2.0)
    return 2.0 * depth_m * math.tan(half_bw_rad)


def swath_width(depth_m: float, swath_deg: float) -> float:
    """
    Across-track swath width [m] (assuming symmetric fan).
    W ≈ 2 * D * tan(theta / 2)
    """
    half_swath_rad = math.radians(swath_deg / 2.0)
    return 2.0 * depth_m * math.tan(half_swath_rad)


def compute_detection_limit(along_step: float, across_step: float) -> float:
    """
    Very simple geometric estimate of the smallest cubic-feature edge length [m]
    that can be reliably sampled by the sounding grid.

    We take the grid cell diagonal:
        diag = sqrt(d^2 + S^2)
    where d is the along-track step between pings and S is the line spacing.

    A cube with edge ≈ diag / 4 will, in practice, be intersected by multiple
    soundings, which is broadly consistent with node-density style rules used
    in NOAA HSSD (minimum soundings per node, limited propagation distance). :contentReference[oaicite:5]{index=5}

    NOTE: S-44 Annex C does not dictate this exact formula; this is a pragmatic
    engineering rule-of-thumb that turns S-44 cube sizes into a spacing check.
    """
    diag = math.hypot(along_step, across_step)
    return diag / 4.0



def compute_tvu(depth_m: float, order_name: str) -> float | None:
    """Compute TVU_max(d) for the chosen order, if a/b are known."""
    ab = ORDER_TVU_AB.get(order_name)
    if not ab:
        return None
    a, b = ab
    return math.sqrt(a * a + (b * depth_m) ** 2)


# -----------------------------------------------------------------------------
# Streamlit app
# -----------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="Maximum Planned Vessel Speed – IHO Orders + Sonar Dead Time",
        layout="wide",
    )

    st.title("Maximum Planned Vessel Speed (IHO Orders + Sonar Dead Time)")

    with st.sidebar:
        st.header("Survey & System Inputs")

        depth = st.number_input("Depth D (m)", value=100.0, min_value=0.0, step=1.0)
        swath_deg = st.number_input("Total Swath (deg)", value=120.0, min_value=0.0, step=1.0)
        beam_width_deg = st.number_input("Tx Beam Width φᵀ (deg)", value=1.5, min_value=0.0, step=0.1)
        sound_speed = st.number_input("Sound Speed c (m/s)", value=1500.0, min_value=0.0, step=1.0)

        st.markdown("---")
        st.header("IHO Coverage / Overlap")

        order_preset = st.selectbox(
            "IHO Survey Order Preset",
            ORDERS,
            index=0,
        )

        preset_cov = ORDER_COVERAGE[order_preset]
        if preset_cov is None:
            coverage_pct_user = st.number_input(
                "Seafloor coverage (S-44 style, %)",
                help="100 % = no overlap, 200 % ≈ 50 % overlap, 300 % ≈ 67 % overlap, etc.",
                value=200.0,
                min_value=100.0,
                max_value=400.0,
                step=10.0,
            )
        else:
            # Lock coverage for IHO preset; just display the value
            coverage_pct_user = float(preset_cov)
            st.text(
                f"Seafloor coverage (S-44 style, %) – fixed by preset: "
                f"{coverage_pct_user:.0f} %"
            )

        # Turning / manoeuvre margin: XOCEAN often assumes about −5 % coverage tolerance.
        turn_margin = st.slider(
            "Turning / manoeuvre overlap tolerance (−% on coverage)",
            min_value=0.0,
            max_value=10.0,
            value=0.0,
            step=0.5,
            help="Reduces the *effective* coverage requirement to allow for "
                 "turning gaps and real-world steering.",
        )

        # Effective coverage used for the speed calculation
        effective_coverage_pct = max(100.0, coverage_pct_user - turn_margin)
        C_eff = effective_coverage_pct / 100.0

        st.caption(
            f"Requested coverage: **{coverage_pct_user:.0f} %**, "
            f"turning tolerance: **{turn_margin:.1f} %**, "
            f"effective coverage used in speed calc: **{effective_coverage_pct:.1f} %**."
        )

        st.markdown("---")

        # Bottom type: used to optionally tighten feature-detection requirement
        bottom_type = st.selectbox(
            "Bottom type (for feature detection)",
            list(BOTTOM_TYPE_FACTORS.keys()),
            index=0,
            help=(
                "S-44 cube sizes (0.5, 1, 2 m or 10 % of depth) are minimum "
                "requirements. For rocky or obstruction-prone ground many "
                "authorities apply stricter detection thresholds."
            ),
        )
        bottom_type_factor = BOTTOM_TYPE_FACTORS[bottom_type]


        st.header("Sonar & Dead Time")

        sonar_choice = st.selectbox(
            "Sonar System",
            list(SONAR_DEAD_TIMES.keys()),
            index=0,
        )

        preset_dt = SONAR_DEAD_TIMES[sonar_choice]

        # If we have a preset, lock it; otherwise let the user enter a custom value
        if preset_dt is not None:
            dead_time = preset_dt
            st.write(f"Preset dead time Δt for **{sonar_choice}**: `{dead_time:.3f}` s")
        else:
            dead_time = st.number_input(
                "Dead Time Δt (s)",
                value=0.15,
                min_value=0.0,
                step=0.01,
                help="Additional time per ping not used by acoustic travel "
                     "(e.g., processing, file IO, safety margin).",
        )

    # ---------- Speed planning (safety factor) ----------
    st.markdown("### Speed planning")

    speed_sf = st.slider(
        "Speed safety factor (fraction of max speed used for optimum planning)",
        min_value=0.40,
        max_value=1.00,
        value=0.80,
        step=0.05,
        help=(
            "Example: 0.80 means optimum speed = 80% of the computed max speed. "
            "Minimum operational speed is MIN_SPEED_RATIO of that optimum."
        ),
    )



    # -------------------------------------------------------------------------
    # Main panel
    # -------------------------------------------------------------------------
    st.markdown("### Calculation")

    mode = st.radio(
        "Mode",
        ["Speed calculator", "Survey planner"],
        horizontal=True,
    )

    # Survey-planner-specific inputs (area and overhead)
    daily_hours = None
    weather_downtime_pct = None
    fuel_burn_lph = None
    fuel_price = None
    area_length_m = None
    area_width_m = None
    overhead_pct = None

    if mode == "Survey planner":
        st.subheader("Survey-area parameters")

        col1, col2 = st.columns(2)
        with col1:
            area_length_m = st.number_input(
                "Area length along-track (m)",
                min_value=0.0,
                value=5000.0,
                step=100.0,
            )
            daily_hours = st.number_input(
                "Planned survey hours per day",
                min_value=1.0,
                max_value=24.0,
                value=12.0,
                step=0.5,
            )
        with col2:
            area_width_m = st.number_input(
                "Area width across-track (m)",
                min_value=0.0,
                value=2000.0,
                step=100.0,
            )
            weather_downtime_pct = st.number_input(
                "Weather / operational downtime allowance (%)",
                min_value=0.0,
                max_value=200.0,
                value=20.0,
                step=5.0,
            )

        overhead_pct = st.number_input(
            "Line-change / manoeuvre overhead (%)",
            min_value=0.0,
            max_value=200.0,
            value=15.0,
            step=5.0,
            help="Allowance for line-changes, turns, checks, etc.",
        )

        fuel_burn_lph = st.number_input(
            "Fuel burn at survey speed (L/h, optional)",
            min_value=0.0,
            value=0.0,
            step=10.0,
        )

    fuel_price = st.number_input(
            "Fuel price (per L, optional)",
            value=0.0,
            min_value=0.0,
            step=0.1,
        )


    run_calc = st.button("Calculate maximum vessel speed")

    if run_calc:
        try:
            # -------- Sanity checks --------
            if depth <= 0 or sound_speed <= 0:
                st.error("Depth and sound speed must be positive.")
                return

            if effective_coverage_pct < 100.0:
                st.error("Effective coverage cannot be < 100 %. Check your turning tolerance.")
                return

            # -------- Core geometry & timing --------
            T = ping_interval(depth, sound_speed, dead_time, swath_deg)  # [s]
            L = along_track_footprint(depth, beam_width_deg)  # [m]
            W = swath_width(depth, swath_deg)  # [m]

            # Coverage to along-track step:
            # C_eff = W / S   (S = line spacing). For along-track we re-use C_eff.
            advance_fraction = 1.0 / C_eff
            along_step = advance_fraction * L                      # [m per ping]
            speed_ms = along_step / T                              # [m/s]
            speed_kts = speed_ms * KNOTS_PER_M_S

            # Convert to planning speeds (max, optimum, minimum)
            v_ms = speed_ms
            v_knots = speed_kts

            v_opt_ms = v_ms * speed_sf
            v_opt_knots = v_knots * speed_sf

            v_min_ms = v_opt_ms * MIN_SPEED_RATIO
            v_min_knots = v_opt_knots * MIN_SPEED_RATIO


            # Across-track line spacing & overlap (for info + planner mode)
            line_spacing = W / C_eff                               # [m]
            overlap_frac = max(0.0, 1.0 - line_spacing / W)
            overlap_pct = overlap_frac * 100.0

            # Annex-C / S-44 style feature-detection check
            det_req = s44_cubic_feature_requirement(order_preset, depth)
            det_limit = None
            det_req_eff = None
            if det_req is not None:
                # Tighten requirement for rough / rocky bottoms if requested
                det_req_eff = det_req * bottom_type_factor
                det_limit = compute_detection_limit(along_step, line_spacing)

            tvu_val = compute_tvu(depth, order_preset)

            # -----------------------------------------------------------------
            # Output: Speed calculator mode
            # -----------------------------------------------------------------
            if mode == "Speed calculator":
                st.success(f"Maximum planned vessel speed ≈ **{speed_kts:.2f} kn**")

                st.markdown("#### Derived parameters")
                st.write(
                    f"- Ping interval **T** ≈ `{T:.3f}` s  "
                    f"(includes Δt = {dead_time:.3f} s)\n"
                    f"- Along-track footprint **L** ≈ `{L:.2f}` m\n"
                    f"- Effective coverage **C_eff** = `{C_eff:.2f}×` "
                    f"(= {effective_coverage_pct:.1f} %)\n"
                    f"- Along-track step between pings **d** ≈ `{along_step:.2f}` m\n"
                    f"- Swath width **W** ≈ `{W:.1f}` m\n"
                    f"- Line spacing implied by coverage **S** ≈ `{line_spacing:.1f}` m\n"
                    f"- Across-track overlap ≈ `{overlap_pct:.1f} %`"

                )
                st.info(
                    f"Optimum speed ≈ **{v_opt_knots:.2f} kn**, "
                    f"Operational min ≈ **{v_min_knots:.2f} kn**"
                )


            # -----------------------------------------------------------------
            # Output: Survey-planner mode
            # -----------------------------------------------------------------
            else:
                if (
                        area_length_m is None
                        or area_width_m is None
                        or overhead_pct is None
                        or daily_hours is None
                        or weather_downtime_pct is None
                ):
                    st.error("Missing survey-area / time inputs.")
                    return

                n_lines = max(1, math.ceil(area_width_m / line_spacing))
                line_length_km = area_length_m / 1000.0
                total_track_km = n_lines * line_length_km

                # speed_kts [kn] -> km/h = kn * 1.852
                if speed_kts <= 0:
                    st.error("Computed speed is non-positive; check inputs.")
                    return
                # Pure sailing time at survey speed
                survey_hours = total_track_km / (speed_kts * KM_PER_NM)

                # Add line-change / inefficiency overhead
                survey_hours_eff = survey_hours * (1.0 + overhead_pct / 100.0)

                # Add weather / operational downtime allowance
                survey_hours_weather = survey_hours_eff * (1.0 + weather_downtime_pct / 100.0)

                # Convert to days based on planned daily work hours
                survey_days = survey_hours_weather / daily_hours

                st.markdown("### Planner Mode")

                st.success(
                    "## Planner Mode\n"
                    f"- Maximum speed ≈ **{speed_kts:.2f} kn**\n"
                    f"- No. of lines ≈ **{n_lines}**\n"
                    f"- Total sailing ≈ **{total_track_km:.1f} km**\n"
                    f"- Pure survey time ≈ **{survey_hours:.1f} h**\n"
                    f"- With overhead ≈ **{survey_hours_eff:.1f} h**\n"
                    f"- With weather allowance ≈ **{survey_hours_weather:.1f} h**\n"
                    f"- Time estimate ≈ **{survey_days:.1f} days** (@ {daily_hours:.1f} h/day)"

                )


                st.markdown("#### Speed summary")

                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.metric("Max Speed (m/s)", f"{v_ms:.3f}")
                with col_b:
                    st.metric("Max Speed (knots)", f"{v_knots:.3f}")
                with col_c:
                    st.metric(
                        "Advance per Ping",
                        f"{advance_fraction * 100:.1f} % of footprint",
                    )

                col_d, col_e, col_f = st.columns(3)
                with col_d:
                    st.metric("Optimum Speed (m/s)", f"{v_opt_ms:.3f}")
                with col_e:
                    st.metric("Optimum Speed (knots)", f"{v_opt_knots:.3f}")
                with col_f:
                    st.metric(
                        "Operational Min Speed (knots)",
                        f"{v_min_knots:.3f}",
                    )


                # Optional: fuel estimate
                fuel_l = None
                fuel_cost = None
                if fuel_burn_lph and fuel_burn_lph > 0.0:
                    fuel_l = fuel_burn_lph * survey_hours_weather
                    if fuel_price and fuel_price > 0.0:
                        fuel_cost = fuel_l * fuel_price

                if fuel_l is not None:
                    st.markdown("#### Fuel & cost (optional)")
                    txt = f"- Estimated fuel usage ≈ `{fuel_l:,.0f}` L"
                    if fuel_cost is not None:
                        txt += f" (≈ `{fuel_cost:,.0f}` in fuel)."
                    st.write(txt)

                st.markdown("#### Line geometry")
                st.write(
                    f"- Swath width **W** ≈ `{W:.1f}` m  "
                    f"- Line spacing **S** ≈ `{line_spacing:.1f}` m\n"
                    f"- Across-track overlap ≈ `{overlap_pct:.1f} %`\n"
                    f"- Along-track step **d** ≈ `{along_step:.2f}` m"
                )

            # -----------------------------------------------------------------
            # Annex-C style detection + TVU info (both modes)
            # -----------------------------------------------------------------
            st.markdown("#### IHO Annex-C style checks (indicative only)")

            if det_req is not None and det_limit is not None:
                if det_limit <= det_req:
                    st.success(
                        f"Indicative cubic-feature detection: grid spacing could "
                        f"detect cubes of ~**{det_limit:.2f} m** edge, which is "
                        f"≤ required **{det_req:.2f} m** for **{order_preset}**."
                    )
                else:
                    st.warning(
                        f"Indicative cubic-feature detection: grid spacing implies "
                        f"detectable cube size ~**{det_limit:.2f} m**, which is "
                        f"> required **{det_req:.2f} m** for **{order_preset}**. "
                        f"Consider increasing coverage or reducing speed."
                    )
            else:
                st.info(
                    "No explicit cubic-feature requirement for this order, "
                    "or logic not implemented (this is a simplified Annex-C check)."
                )

            if tvu_val is not None:
                st.caption(
                    f"TVU_max(d) for {order_preset} at D = {depth:.1f} m "
                    f"(a,b from S-44 Table 1): **{tvu_val:.2f} m**."
                )

        except Exception as exc:  # noqa: BLE001
            st.error(f"Something went wrong in the calculation: {exc}")

        # -----------------------------------------------------------------
        # Reference / Standard Info (footer)
        # -----------------------------------------------------------------
        st.markdown("---")
        with st.expander("References / Standards Used"):
            st.markdown(REFERENCES_HELP)


if __name__ == "__main__":
    main()
