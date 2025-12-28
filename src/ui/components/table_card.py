import customtkinter as ctk

class TableCard(ctk.CTkFrame):
    def __init__(self, master, table_data, current_dsn, target_dsn, app_instance, side, **kwargs):
        super().__init__(master, fg_color="#1e1e1e", corner_radius=12, border_width=1, border_color="#333", **kwargs)
        self.table_name = table_data['name']; self.rows = table_data['rows']
        self.current_dsn = current_dsn; self.target_dsn = target_dsn; self.app = app_instance; self.side = side

        # Checkbox for multi-selection
        self.check_var = ctk.BooleanVar(value=False)
        self.cb = ctk.CTkCheckBox(self, text="", variable=self.check_var, width=20, command=self.on_toggle)
        self.cb.pack(side="left", padx=(10, 0))

        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(info_frame, text=self.table_name, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x")
        
        stats_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
        stats_frame.pack(fill="x")
        ctk.CTkLabel(stats_frame, text=f" {table_data['size']} ", fg_color="#34495e", corner_radius=10, font=("Consolas", 9)).pack(side="left", padx=2)
        ctk.CTkLabel(stats_frame, text=f" {self.rows:,} rows ", fg_color="#27ae60", corner_radius=10, font=("Consolas", 9)).pack(side="left", padx=2)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(side="right", padx=10)
        ctk.CTkButton(actions, text="🚀", width=35, fg_color="#d35400", command=lambda: self.app.initiate_bulk_migration(self.side, [self.table_name])).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="🗑", width=35, fg_color="#7b241c", command=lambda: self.app.initiate_bulk_delete(self.side, [self.table_name])).pack(side="left", padx=2)

    def on_toggle(self):
        self.app.update_selection_count(self.side)
