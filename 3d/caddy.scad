// esp32cam_case_slider.scad
// Parametric ESP32-CAM case with sliding lens cover + mounting ears
// Designed as an original model (not a copy of any specific seller part).

$fn = 64;

// ---------- Parameters (EDIT THESE) ----------
board_w = 27.0;        // ESP32-CAM PCB width (mm)
board_l = 40.5;        // PCB length (mm)
board_t = 1.6;         // PCB thickness (mm)

clear_xy = 0.6;        // clearance around board (mm) (increase if too tight)
clear_z  = 1.0;        // clearance above board components (mm)

wall = 2.0;            // case wall thickness (mm)
base_floor = 2.0;      // bottom thickness (mm)

inner_h = 12.0;        // internal height from floor to inside top (mm)
                      // ensure this clears your tallest component

lens_window_w = 14.0;  // window width (mm)
lens_window_h = 14.0;  // window height (mm)
lens_window_offset_x = 0.0; // shift window left/right (mm)
lens_window_offset_y = 9.0; // shift window toward "front" (mm)

rail_h = 2.2;          // slider rail height (mm)
rail_w = 1.8;          // slider rail thickness (mm)
rail_gap = 0.3;        // clearance between rail and slider (mm)

slider_t = 1.6;        // slider plate thickness (mm)
slider_grip = 6.0;     // grip tab length (mm)

ear_w = 10.0;          // mounting ear width (mm)
ear_t = 4.0;           // mounting ear thickness (mm)
ear_hole_d = 3.2;      // M3 clearance
ear_offset = 6.0;      // ear offset from case side (mm)

corner_r = 3.0;        // rounding radius (cosmetic)

// ---------- Derived ----------
inner_w = board_w + 2*clear_xy;
inner_l = board_l + 2*clear_xy;
outer_w = inner_w + 2*wall;
outer_l = inner_l + 2*wall;
outer_h = base_floor + inner_h;

module rounded_box(w,l,h,r){
  // Minkowski rounding (simple + nice)
  minkowski(){
    cube([w-2*r, l-2*r, h], center=false);
    cylinder(r=r, h=0.01);
  }
}

module case_body(){
  difference(){
    // outer shell
    rounded_box(outer_w, outer_l, outer_h, corner_r);

    // hollow interior
    translate([wall, wall, base_floor])
      cube([inner_w, inner_l, inner_h+0.1], center=false);

    // front lens window cut (through the "lid area")
    // Window positioned on the "top face" area, but we cut through the front wall area
    // by cutting a vertical slot through the top thickness region.
    translate([
      outer_w/2 - lens_window_w/2 + lens_window_offset_x,
      wall + lens_window_offset_y,
      outer_h - (wall + 0.1)
    ])
      cube([lens_window_w, lens_window_h, wall+0.2], center=false);

    // Simple cable/connector notch at the back (tweak as needed)
    notch_w = 12;
    notch_h = 6;
    translate([outer_w/2 - notch_w/2, outer_l - wall - 0.1, base_floor+2])
      cube([notch_w, wall+0.2, notch_h], center=false);
  }

  // Rails for slider (two rails on the top face, left/right)
  // Rails run along Y direction near the top
  rail_z = outer_h - wall - rail_h;

  // left rail
  translate([wall + 1.0, wall, rail_z])
    cube([rail_w, outer_l - 2*wall, rail_h], center=false);

  // right rail
  translate([outer_w - wall - 1.0 - rail_w, wall, rail_z])
    cube([rail_w, outer_l - 2*wall, rail_h], center=false);

  // Mounting ears (left/right mid)
  translate([-ear_offset, outer_l/2 - ear_w/2, 0])
    mounting_ear();

  translate([outer_w + ear_offset - ear_t, outer_l/2 - ear_w/2, 0])
    mounting_ear();
}

module mounting_ear(){
  difference(){
    cube([ear_t, ear_w, base_floor + inner_h/2], center=false);
    translate([ear_t/2, ear_w/2, (base_floor + inner_h/4)])
      rotate([0,90,0])
        cylinder(d=ear_hole_d, h=ear_t+0.5, center=true);
  }
}

module slider(){
  // Slider plate sits over the top, captured by rails
  plate_w = outer_w - 2*(wall + 1.0) - 2*rail_w - 2*rail_gap;
  plate_l = outer_l - 2*wall;

  // Plate thickness: slider_t
  // Add a "shutter" region that covers the lens window when slid forward
  union(){
    // main plate
    translate([wall + 1.0 + rail_w + rail_gap, wall, outer_h - wall - slider_t])
      cube([plate_w, plate_l, slider_t], center=false);

    // grip tab at the back
    translate([outer_w/2 - 10/2, outer_l - wall + 0.1, outer_h - wall - slider_t])
      cube([10, slider_grip, slider_t], center=false);

    // stopper nib (front) to reduce slipping out
    translate([outer_w/2 - 6/2, wall - 1.2, outer_h - wall - slider_t])
      cube([6, 1.2, slider_t], center=false);
  }
}

// ---------- Build ----------
case_body();
// For printing the slider separately, comment the line above and uncomment below:
// slider();